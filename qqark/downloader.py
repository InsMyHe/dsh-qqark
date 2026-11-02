"""下载队列。

设计要点（都是踩坑后的必要设计）：
  - 群文件/视频链接带 rkey，几分钟就失效 → 下载失败若判定为 403/过期，重新调 API 换新 url 再试
  - 断点续传：临时文件 .part + Range 请求，进程被杀也能续
  - 去重：先按 (group_id,file_id) 查索引，再可选按 size 查
  - 并发：固定 N 个 worker 线程，避免把 NapCat / QQ 打挂
  - 落盘：先写 data/tmp/ → 校验大小 → 原子移动到最终分类目录
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .classify import build_relpath, category_of, safe_name
from .config import Config
from .db import Database
from .onebot import OneBotClient, OneBotError

log = logging.getLogger("qqark.downloader")

_URL_SCHEMES = ("http://", "https://")


class _Gone(Exception):
    """文件已被确认不在群里了 —— 不是"这次没成功"，是"以后也不会成功"。

    为什么单独一个异常：实测 QQ 群文件有保留期，8-9 月的老文件实体被服务端
    清理后，列表里还留着记录，但换链接会稳定报
    `sendGroupFileDownloadReq error: 文件不存在 (code=-103)`。
    把它和普通网络/超时失败混在一起会带来两个后果：
      1. 任务在 failed 里堆着，用户以为"还有 74 个能下"，其实永远下不来；
      2. 每次点了重试都重新遍历一遍群目录（一次几十个 API 调用）。
    所以给它一个终态 `gone`，界面能明确展示"群里已经没有了"。
    """


def _as_download_url(raw: object) -> str:
    """把 NapCat 返回的「url」字段洗成真正的可下载 http(s) 链接。

    为什么需要这一步（真实事故）：NapCat 的 `get_file` 并不保证返回 URL。
    当 packetBackend 不可用（例如 QQ 客户端被自动升级到 NapCat 不支持的版本）
    时，它返回的是**本地缓存路径**，形如 `C:\\Users\\...\\xxx.mp4`。
    老代码直接把这段字符串丢给 urllib.request.Request，报出来的错误是

        URLError: <urlopen error unknown url type: c>

    ——那个 `c` 就是 `C:` 的盘符字母，完全看不出是「NapCat 已经坏了」。
    所以这里只放行 http/https，其余一律当没拿到，并记下原值方便排查。
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    lower = text.lower()
    if lower.startswith(_URL_SCHEMES):
        return text
    if len(text) > 3 and text[1] == ":" and text[2] in ("\\", "/"):
        log.debug("丢弃本地路径（不是可下载链接）：%s", text)
    else:
        log.debug("丢弃无法识别的链接：%s", text[:120])
    return ""


class Downloader:
    def __init__(self, cfg: Config, db: Database, ob: OneBotClient):
        self.cfg = cfg
        self.db = db
        self.ob = ob
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._wake = threading.Event()
        self._tmp_dir = cfg.data_dir / "tmp"
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._active = 0
        self._active_lock = threading.Lock()
        self._done_count = 0
        self._fail_count = 0
        self._stat_lock = threading.Lock()
        self._recent: list[dict] = []   # 最近完成/失败记录，供 UI 显示

    # ---------- 生命周期 ----------

    def start(self) -> None:
        n = max(1, int(self.cfg.get("concurrency") or 3))
        self._stop.clear()
        for i in range(n):
            t = threading.Thread(target=self._worker, name=f"dl-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        log.info("下载器已启动，并发 %d", n)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for t in self._workers:
            t.join(timeout=3)
        self._workers.clear()

    def notify(self) -> None:
        """有新任务时唤醒 worker。"""
        self._wake.set()

    # ---------- 状态 ----------

    def status(self) -> dict:
        with self._active_lock:
            active = self._active
        with self._stat_lock:
            recent = list(self._recent[-20:])
        # 已完成/失败 一律以数据库为准，不用进程内计数器。
        # 0.5.2 修：原来 done/failed 用的是 self._done_count/_fail_count，
        # 这是「本次进程跑出来的数」——归档器一重启就归零。实测重启后界面
        # 显示 pending=0 done=0 failed=0，而库里明明有 146 个 done、162 个失败，
        # 用户看到的是"什么都没发生"。pending/running 一直是查库的，所以只有
        # 这两个数在骗人，更容易让人误判。
        return {
            "active": active,
            "done": self.db.count_tasks("done"),
            "failed": self.db.count_tasks("failed"),
            "pending": self.db.count_tasks("pending"),
            "running": self.db.count_tasks("running"),
            # 群里已经没有了、永远下不来的那些。单独报出去，别混进 failed，
            # 否则界面上的「失败」数字会一直骗人（见 _Gone 的说明）。
            "gone": self.db.count_tasks("gone"),
            "recent": recent,
            # 本次进程启动以来的增量，界面上另作展示，不和上面的「总量」混用。
            "session_done": self._done_count,
            "session_failed": self._fail_count,
        }

    def _record(self, item: dict) -> None:
        with self._stat_lock:
            self._recent.append(item)
            if len(self._recent) > 200:
                del self._recent[:100]

    # ---------- worker ----------

    def _ob_ready(self, account: str | None) -> bool:
        """该账号（None=任意账号）当前是否连着，能换到下载链接。"""
        try:
            return bool(self.ob.connected(account))
        except Exception:
            return False

    def _worker(self) -> None:
        while not self._stop.is_set():
            # 没有任何账号连上时绝不能领任务。
            # 换下载链接必须走 NapCat 的 get_group_file_url，没连接就一定失败；
            # 更糟的是失败会被计入 attempts，三轮之后任务被标成 failed，
            # 而文件其实是好的（实测 20 个真实文件就是这么被误杀的）。
            # 所以这里宁可空转等连接，也不要碰队列。
            if not self._ob_ready(None):
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            try:
                tasks = self.db.next_tasks(4)
            except Exception:
                log.exception("读取任务失败")
                tasks = []
            if not tasks:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            for task in tasks:
                if self._stop.is_set():
                    return
                # 多账号场景：任务只能由"当初收到它的那个号"去换链接（用错号 403）。
                # 那个号没连上就先留着，等它上线，不算失败。
                acc = str(task.get("account") or "") or None
                if acc and not self._ob_ready(acc):
                    continue
                if not self.db.claim_task(task["id"]):
                    continue
                with self._active_lock:
                    self._active += 1
                try:
                    self._process(task)
                except Exception as exc:
                    log.exception("任务处理异常 #%s", task.get("id"))
                    self._on_fail(task, str(exc))
                finally:
                    with self._active_lock:
                        self._active -= 1
                self._wake.clear()

    # ---------- 单任务 ----------

    def _process(self, task: dict) -> None:
        file_name = task.get("file_name") or f"unnamed_{task['id']}"
        size = int(task.get("size") or 0)
        group_id = str(task.get("group_id") or "")

        # 过滤
        min_size = int(self.cfg.get("min_size") or 0)
        if min_size and size and size < min_size:
            self.db.finish_task(task["id"], "skipped", "小于最小体积限制")
            return
        max_mb = int(self.cfg.get("max_size_mb") or 0)
        if max_mb and size > max_mb * 1024 * 1024:
            self.db.finish_task(task["id"], "skipped", f"超过 {max_mb}MB 上限")
            return

        # 已有索引 → 跳过（避免补扫反复重下）。按 dedupe_key 判断，
        # 因为 file_id 每次列举都会变，按它判断拦不住重复。
        dkey = str(task.get("dedupe_key") or "")
        if dkey:
            if self.db.conn().execute(
                "SELECT 1 FROM files WHERE group_id=? AND dedupe_key=? LIMIT 1",
                (group_id, dkey),
            ).fetchone():
                self.db.finish_task(task["id"], "skipped", "已存在")
                return
        elif task.get("file_id") and self.db.file_exists(task["file_id"], group_id):
            self.db.finish_task(task["id"], "skipped", "已存在")
            return

        retry = max(1, int(self.cfg.get("retry") or 3))
        delay = max(1, int(self.cfg.get("retry_delay") or 5))
        last_err = ""
        url = _as_download_url(task.get("url") or "")

        for attempt in range(1, retry + 1):
            if self._stop.is_set():
                self.db.retry_task(task["id"])
                return
            try:
                # 每次尝试都重新取 url：rkey 过期后旧 url 必然 403
                if not url:
                    url = self._resolve_url(task)
                saved = self._download_one(task, url)
                if saved:
                    self._on_success(task, saved)
                    self.db.finish_task(task["id"], "done")
                    return
                last_err = "未知失败"
            except _UrlExpired as exc:
                last_err = f"链接过期: {exc}"
                log.info("链接过期，重新获取 url（第 %d 次）: %s", attempt, file_name)
                url = ""
            except _Gone as exc:
                # 终态：文件在群里已经没了，重试只会一遍遍白扫目录树。
                log.info("群里已无此文件，标记为 gone：%s（%s）", file_name, exc)
                self.db.finish_task(task["id"], "gone", str(exc))
                return
            except _Skip as exc:
                self.db.finish_task(task["id"], "skipped", str(exc))
                return
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log.warning("下载失败（第 %d/%d 次）%s: %s", attempt, retry, file_name, last_err)
                url = ""

            if attempt < retry:
                for _ in range(delay * 10):
                    if self._stop.is_set():
                        self.db.retry_task(task["id"])
                        return
                    time.sleep(0.1)

        self._on_fail(task, last_err)

    def _resolve_url(self, task: dict) -> str:
        group_id = str(task.get("group_id") or "")
        file_id = task.get("file_id") or ""
        busid = int(task.get("busid") or 0)
        # 关键：必须用"当初收到这个任务的账号"去换链接。
        # 用错账号会因为没有群成员/访问权限而 403。
        acc = str(task.get("account") or "") or None

        if task.get("kind") == "chat" or not file_id:
            # 聊天消息里的视频/文件：从 get_file 拿 url
            url = self._url_from_get_file(file_id, acc) if file_id else ""
            if url:
                return url
            raise OneBotError("无法解析媒体链接（file_id 为空或 get_file 无 url）")

        url = self._try_group_file_url(group_id, file_id, busid, acc)
        if url:
            return url

        # 换不到链接的常见原因是 file_id 过期（NapCat 返回
        # `retcode=1200 real fileUUID not found!`）。file_id 每次列举群文件都会
        # 重新生成，所以唯一的出路是重新遍历群目录，按文件名找回当前有效的
        # file_id，把新值写回任务再换一次链接。
        refreshed, scan_failed = self._refresh_file_ref(task, acc)
        if refreshed:
            file_id, busid = refreshed
            url = self._try_group_file_url(group_id, file_id, busid, acc)
            if url:
                return url

        # 最后再退一步：群文件也可能能通过 get_file 拿到
        url = self._url_from_get_file(file_id, acc)
        if not url:
            if scan_failed:
                # 目录没读全就报"文件不存在"会把真实文件永久判死，说得保守一点，
                # 让上层的重试/补扫还有机会再来一次。
                raise OneBotError(
                    "换不到下载链接（file_id 已过期，重新列举群文件时部分目录读取失败，"
                    "暂不能确定文件是否还在；稍后会自动重试）"
                )
            if not refreshed:
                # 走到了这里说明三件事同时成立：①老 file_id 换不到链接；
                # ②重新列举的整棵目录树读得干干净净（没有 scan_failed）；
                # ③这棵树里根本没有这个文件名（refreshed 为空）。
                # 那它就是真的没了 —— QQ 群文件过期清理后，列表里还留着名字，
                # 但文件体已被删除，换链接永远 `文件不存在 (code=-103)`。
                # 判成 gone 而不是 failed，否则用户会以为"再等等就能下"。
                raise _Gone("文件已不在群里（群文件过期或已被管理员删除）")
            raise OneBotError(
                "换不到下载链接（file_id 已过期且重新列举群文件也没找到该文件；"
                "可能是文件已被删除或无权访问）"
            )
        return url

    def _try_group_file_url(self, group_id: str, file_id: str, busid: int,
                            acc: str | None) -> str:
        try:
            return _as_download_url(self.ob.get_group_file_url(
                group_id, file_id, busid, self_id=acc) or "")
        except OneBotError as exc:
            log.info("get_group_file_url 失败（file_id=%s）: %s", file_id[:8], exc)
            return ""

    def _refresh_file_ref(self, task: dict,
                          acc: str | None) -> tuple[tuple[str, int] | None, bool]:
        """重新列举群文件，按 文件名+大小 找回当前有效的 (file_id, busid)。

        返回 (结果, 扫描是否出错)。第二项用来区分"文件真没了"和"目录没读全"，
        后者不该把任务判死。
        """
        group_id = str(task.get("group_id") or "")
        file_name = task.get("file_name") or ""
        if not group_id or not file_name:
            return None, False
        finder = getattr(self.ob, "find_group_file", None)
        if finder is None:
            return None, False
        try:
            hit = finder(group_id, file_name, int(task.get("size") or 0),
                         self_id=acc, max_depth=int(self.cfg.get("scan_depth") or 3))
        except Exception as exc:  # 列举失败不该让任务直接失败，让上层重试
            log.info("重新列举群文件失败 %s/%s: %s", group_id, file_name, exc)
            return None, True
        scan_errors = list(getattr(self.ob, "_scan_errors", []) or [])
        if not hit:
            if scan_errors:
                log.info("列举群文件有 %d 个目录读取失败，暂不下结论：%s",
                         len(scan_errors), scan_errors[0])
            return None, bool(scan_errors)
        new_id = hit.get("file_id") or hit.get("id") or ""
        new_busid = int(hit.get("busid") or 0) or int(task.get("busid") or 0)
        if not new_id:
            return None, bool(scan_errors)
        log.info("刷新 file_id：%s -> %s（%s）",
                 (task.get("file_id") or "")[:8], new_id[:8], file_name)
        try:
            tid = task.get("id")
            if tid:
                self.db.update_task_file_ref(int(tid), new_id, new_busid)
                task["file_id"] = new_id
                task["busid"] = new_busid
        except Exception:
            log.debug("写回 file_id 失败", exc_info=True)
        return (new_id, new_busid), False

    def _url_from_get_file(self, file_id: str, self_id: str | None = None) -> str:
        try:
            data = self.ob.get_file(file_id, self_id=self_id)
        except OneBotError as exc:
            log.warning("get_file 失败: %s", exc)
            return ""
        return _as_download_url(data.get("url") or "")

    def _download_one(self, task: dict, url: str) -> dict | None:
        group_id = str(task.get("group_id") or "")
        group_name = task.get("group_name") or f"群{group_id}"
        file_name = task.get("file_name") or "unnamed"
        size = int(task.get("size") or 0)

        root = self.cfg.save_root
        rel = build_relpath(
            root=root,
            layout=self.cfg.get("layout") or "group_date_type",
            group_name=group_name,
            group_id=group_id,
            file_name=file_name,
            ts=task.get("created_at") or time.time(),
            source=task.get("source") or "群文件",
            uploader=task.get("uploader") or "",
            uniq=str(task["id"]),
        )

        # 已存在且大小一致 → 跳过
        if self.cfg.get("skip_existing") and rel.exists():
            existing = rel.stat().st_size
            if not size or existing == size:
                raise _Skip("目标文件已存在")

        tmp = self._tmp_dir / f"t{task['id']}_{safe_name(rel.name, 80)}.part"
        tmp.parent.mkdir(parents=True, exist_ok=True)

        got = self._fetch(url, tmp, size)

        # 大小校验：群文件应完全匹配；聊天视频可能拿不到准数
        if size and got != size:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise IOError(f"大小不符（期望 {size}，实得 {got}）")

        rel.parent.mkdir(parents=True, exist_ok=True)
        rel = _dedupe_target(rel)
        os.replace(tmp, rel)

        return {
            "path": rel,
            "size": got,
            "category": category_of(rel.name),
        }

    def _fetch(self, url: str, tmp: Path, expect_size: int) -> int:
        """带断点续传的下载。返回实际字节数。"""
        timeout = int(self.cfg.get("timeout") or 60)
        chunk = int(self.cfg.get("chunk_size") or 1048576)

        have = tmp.stat().st_size if tmp.exists() else 0
        if expect_size and have == expect_size and have > 0:
            return have
        if expect_size and have > expect_size:
            tmp.unlink(missing_ok=True)
            have = 0

        req = urllib.request.Request(url)
        # 伪装 UA：QQ 的 pdown 链接对默认 UA 偶尔返回 403
        req.add_header("User-Agent",
                       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        req.add_header("Accept", "*/*")
        if have:
            req.add_header("Range", f"bytes={have}-")

        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:
                return have  # Range 越界 = 已下完
            if exc.code in (403, 401, 404, 410):
                raise _UrlExpired(f"HTTP {exc.code}") from exc
            raise

        mode = "r+b" if (have and resp.status == 206) else "wb"
        if mode == "wb":
            have = 0
        elif have and resp.status != 206:
            mode = "wb"
            have = 0

        total = 0
        with open(tmp, mode) as fh:
            fh.seek(have)
            total = have
            while True:
                if self._stop.is_set():
                    raise IOError("已停止")
                block = resp.read(chunk)
                if not block:
                    break
                fh.write(block)
                total += len(block)
        return total

    # ---------- 结果回调 ----------

    def _on_success(self, task: dict, saved: dict) -> None:
        path: Path = saved["path"]
        fid = self.db.add_file(
            group_id=str(task.get("group_id") or ""),
            group_name=task.get("group_name") or "",
            file_id=task.get("file_id") or "",
            busid=int(task.get("busid") or 0),
            file_name=task.get("file_name") or path.name,
            save_path=str(path),
            size=saved["size"],
            category=saved["category"],
            source=task.get("source") or "群文件",
            uploader=task.get("uploader") or "",
            uploader_id=str(task.get("uploader_id") or ""),
            msg_id=str(task.get("msg_id") or ""),
            status="done",
            created_at=time.time(),
            account=str(task.get("account") or ""),
            account_name=str(task.get("account_name") or ""),
            dedupe_key=str(task.get("dedupe_key") or ""),
        )
        with self._stat_lock:
            self._done_count += 1
        log.info("✔ 已保存 [%s] %s → %s (%.1f MB)", task.get("group_name") or task.get("group_id"),
                 task.get("file_name"), path, saved["size"] / 1048576)
        self._record({
            "ok": True, "file_name": task.get("file_name"), "path": str(path),
            "size": saved["size"], "group_name": task.get("group_name") or "",
            "group_id": str(task.get("group_id") or ""), "source": task.get("source") or "",
            "at": time.time(), "db_id": fid,
        })
        # 清理分片
        try:
            for p in self._tmp_dir.glob(f"t{task['id']}_*"):
                p.unlink(missing_ok=True)
        except OSError:
            pass

    def _on_fail(self, task: dict, err: str) -> None:
        self.db.finish_task(task["id"], "failed", err)
        with self._stat_lock:
            self._fail_count += 1
        log.error("✘ 下载失败 %s: %s", task.get("file_name"), err)
        self._record({
            "ok": False, "file_name": task.get("file_name"), "error": err,
            "group_name": task.get("group_name") or "", "group_id": str(task.get("group_id") or ""),
            "source": task.get("source") or "", "at": time.time(),
        })

    # ---------- 队列入口 ----------

    def enqueue(self, **kw) -> int | None:
        """加入下载任务；返回任务 id（重复任务返回 None）。"""
        tid = self.db.add_task(**kw)
        if tid:
            self.notify()
        return tid


class _UrlExpired(Exception):
    """下载链接过期（rkey 失效），需要重新获取。"""


class _Skip(Exception):
    """无需下载（已存在 / 被过滤）。"""


def _dedupe_target(rel: Path) -> Path:
    """若目标已存在，追加 _2/_3… 避免覆盖。"""
    if not rel.exists():
        return rel
    stem, dot, ext = rel.name.rpartition(".")
    if not dot:
        stem, ext = rel.name, ""
    i = 2
    while True:
        candidate = rel.parent / safe_name(f"{stem}_{i}{('.' + ext) if ext else ''}", 150)
        if not candidate.exists():
            return candidate
        i += 1
