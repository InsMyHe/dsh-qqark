"""群文件补扫器。

作用：软件没开 / 掉线期间上传的群文件，通过递归遍历群文件目录补回来。
这是"不漏录"的第二道保险 —— 实时事件是第一道。

注意：只能补"群文件"。聊天里发的视频超时后不可补（链接时效性），
那类只能尽量在事件到达时立刻下载。
"""

from __future__ import annotations

import logging
import threading
import time

from .classify import dedupe_key
from .config import Config
from .db import Database
from .downloader import Downloader
from .events import EventRouter
from .onebot import OneBotClient, OneBotError

log = logging.getLogger("qqark.scanner")


class Scanner:
    def __init__(self, cfg: Config, db: Database, ob: OneBotClient, dl: Downloader, router: EventRouter):
        self.cfg = cfg
        self.db = db
        self.ob = ob
        self.dl = dl
        self.router = router
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self.state = {"running": False, "group": "", "scanned": 0, "queued": 0, "last_run": 0,
                      "error": "", "read_errors": 0, "last_read_error": ""}

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scanner", daemon=True)
        self._thread.start()
        log.info("补扫器已启动，间隔 %s 分钟", self.cfg.get("scan_interval_min"))

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def trigger(self) -> None:
        """手动触发一次全量补扫。"""
        self._wake.set()

    def status(self) -> dict:
        return dict(self.state)

    # ---------- 主循环 ----------

    def _loop(self) -> None:
        # 启动后先等连接建立，再补扫
        if self.cfg.get("scan_on_connect"):
            for _ in range(60):
                if self._stop.is_set():
                    return
                if self.ob.connected():
                    break
                time.sleep(1.0)
            if self.ob.connected():
                time.sleep(3)  # 等 NapCat 初始化完
                self._run_once()

        while not self._stop.is_set():
            interval = max(1, int(self.cfg.get("scan_interval_min") or 30)) * 60
            # 分片等待，便于手动触发
            waited = 0
            while waited < interval and not self._stop.is_set():
                if self._wake.wait(timeout=5):
                    self._wake.clear()
                    if not self._stop.is_set():
                        self._run_once()
                    waited = 0
                    continue
                waited += 5
            if self._stop.is_set():
                return
            self._run_once()

    def _run_once(self) -> None:
        if not self.cfg.get("scan_enabled"):
            return
        if not self.ob.connected():
            log.info("补扫跳过：OneBot 未连接")
            return

        self.state.update({"running": True, "error": "", "scanned": 0, "queued": 0,
                           "last_run": time.time(), "read_errors": 0, "last_read_error": ""})
        # 多账号：每个号都要扫自己的群。同一个群可能被两个号同时加入，靠 file_id 去重。
        accounts = [a["self_id"] for a in self.ob.accounts()] or [""]
        enabled_map = self.db.group_enabled_map()
        watch = {str(w) for w in (self.cfg.get("watch_groups") or [])}
        total_q = 0
        errors: list[str] = []

        for acc in accounts:
            if self._stop.is_set():
                break
            try:
                groups = self.ob.get_group_list(self_id=acc or None)
            except OneBotError as exc:
                log.warning("补扫失败：账号 %s 取群列表出错 %s", acc or "-", exc)
                errors.append(f"{acc or '-'}: {exc}")
                continue

            nick = self.ob._nicknames.get(str(acc), "")
            for g in groups:
                if self._stop.is_set():
                    break
                gid = str(g.get("group_id") or "")
                if not gid:
                    continue
                if not enabled_map.get(gid, True):
                    continue
                if watch and gid not in watch:
                    continue
                name = g.get("group_name") or ""
                self.db.upsert_group(gid, name, int(g.get("member_count") or 0),
                                     account=str(acc or ""), account_name=nick)
                self.state["group"] = name or gid
                try:
                    total_q += self._scan_group(gid, name, acc)
                except Exception as exc:
                    log.warning("补扫群 %s 失败: %s", gid, exc)
                self.db.mark_scanned(gid)

        self.state.update({"running": False, "queued": total_q,
                           "error": "; ".join(errors)[:300]})
        log.info("补扫完成：%d 个账号，新增 %d 个下载任务", len(accounts), total_q)

    def _scan_group(self, group_id: str, group_name: str, self_id: str = "") -> int:
        depth_max = max(1, int(self.cfg.get("scan_depth") or 3))
        queued = 0
        visited: set[str] = set()

        def walk(folder_id: str | None, depth: int) -> None:
            nonlocal queued
            if depth > depth_max or self._stop.is_set():
                return
            try:
                if folder_id is None:
                    data = self.ob.get_group_root_files(group_id, self_id=self_id or None)
                else:
                    if folder_id in visited:
                        return
                    visited.add(folder_id)
                    data = self.ob.get_group_files_by_folder(group_id, folder_id,
                                                             self_id=self_id or None)
            except OneBotError as exc:
                # 不能只 log.debug：这一行曾经是"任务建好了却永远下不来"的元凶 ——
                # 列目录超时后整棵子目录被静默丢弃（debug 级别在 info 配置下根本
                # 看不到），于是群文件在列表里显示有、却被判成不存在。
                # 升成 warning 并记进 errors，补扫状态里能看到，界面上才有线索。
                log.warning("读取目录失败 %s/%s: %s", group_id, folder_id, exc)
                self.state["read_errors"] = int(self.state.get("read_errors") or 0) + 1
                self.state["last_read_error"] = f"{group_id}/{folder_id or 'root'}: {exc}"
                return

            for f in (data.get("files") or []):
                queued += self._maybe_enqueue(group_id, group_name, f, self_id)
                self.state["scanned"] += 1
            for sub in (data.get("folders") or []):
                sid = sub.get("folder_id") or sub.get("id") or ""
                if sid:
                    walk(sid, depth + 1)

        walk(None, 1)
        return queued

    def _maybe_enqueue(self, group_id: str, group_name: str, f: dict,
                       self_id: str = "") -> int:
        file_name = f.get("file_name") or f.get("name") or ""
        file_id = f.get("file_id") or f.get("id") or ""
        if not file_name or not file_id:
            return 0
        size = int(f.get("file_size") or f.get("size") or 0)

        uploader = str(f.get("uploader") or f.get("uploader_name") or "")
        ts = 0.0
        for key in ("upload_time", "modify_time", "create_time"):
            v = f.get(key)
            if isinstance(v, (int, float)) and v:
                ts = float(v)
                break

        # 已抓到过、或已在队列里 → 跳过（补扫的主要成本在这里省下来）
        # 注意不能按 file_id 判断：NapCat 每次列举都会重新生成 file_id
        key = dedupe_key(group_id, file_name, size)
        if self.db.dedupe_exists(group_id, key):
            # 但有个例外：这条记录可能是之前判成 gone 的。
            # 既然这次补扫真的在群里看到它了，说明"群里已无此文件"的判断
            # 是错的（列目录可能分页，只看了一部分就下了结论）。
            # 让它重新排队，否则那个文件永远下不来。
            if self.db.revive_gone(group_id, key):
                log.info("补扫重新发现之前判为 gone 的文件，已放回队列：%s", file_name)
                self.dl.notify()
            return 0

        tid = self.dl.enqueue(
            kind="group_file",
            group_id=group_id,
            group_name=group_name,
            file_id=file_id,
            busid=int(f.get("busid") or 0),
            file_name=file_name,
            size=size,
            uploader=uploader,
            uploader_id=str(f.get("uploader_id") or ""),
            source="群文件",
            created_at=ts or time.time(),
            account=str(self_id or ""),
            account_name=self.ob._nicknames.get(str(self_id), ""),
            dedupe_key=key,
        )
        return 1 if tid else 0
