"""OneBot11 客户端（支持多账号并存）。

两种传输：
  1) 反向 WebSocket（NapCat 主动连我们，默认路径 /ws）—— 本项目的首选
  2) HTTP API（NapCat 开 httpServers 时可用）—— 兜底

**多账号**：每个 NapCat 实例（每个 QQ 号）会各开一条反向 WS 连接，握手头里
带 X-Self-ID。本模块按 self_id 维护连接字典，不互相顶掉 —— 这一点很关键，
因为"群 A 里的文件"只能由"加入了群 A 的那个账号"去查下载链接，用错账号
必然 403。所以 call(..., self_id=...) 要能指定用哪个号发请求。

调用约定：call(action, params, self_id=None) 同步返回 dict；内部用 echo 关联请求与响应。
"""

from __future__ import annotations

import inspect
import itertools
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from .ws_server import OP_TEXT, WSClosed, WSConnection

log = logging.getLogger("qqark.onebot")


class OneBotError(Exception):
    pass


class OneBotClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        # self_id -> WSConnection；self_id 为空时用 "__default__" 作为键
        self._conns: dict[str, WSConnection] = {}
        self._conn_lock = threading.RLock()
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()
        # 群文件目录遍历串行化用（见 find_group_file 的说明）
        self._list_lock = threading.Lock()
        self._scan_errors: list[str] = []
        # 目录树缓存：(group_id, self_id) -> (时间戳, 列表)。见 _list_cache_get 的说明。
        self._list_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._list_cache_ttl = 60.0
        self._echo = itertools.count(1)
        self._http_api = ""
        self._http_token = ""
        self.event_handler = None      # callable(event: dict, self_id: str) 或 callable(event)
        self.on_connect = None         # callable(self_id: str)
        self.on_disconnect = None      # callable(self_id: str)
        self.self_id = ""              # 最近接入的账号（UI 展示用）
        self.nickname = ""
        self._nicknames: dict[str, str] = {}
        self._conn_event = threading.Event()
        self._handler_arity: int | None = None
        # 演示/测试用：强制认为已连接（不要去改类方法，那会污染所有实例）
        self.force_connected = False
        self.force_self_id = ""

    # ---------- 配置 ----------

    def set_http(self, api: str, token: str = "") -> None:
        self._http_api = (api or "").rstrip("/")
        self._http_token = token or ""

    # ---------- 连接管理（供 WS 服务端调用） ----------

    @staticmethod
    def _key(self_id) -> str:
        return str(self_id) if self_id else "__default__"

    def attach(self, conn: WSConnection) -> None:
        """登记一条反向 WS 连接。同一账号重连时替换旧的，不同账号并存。"""
        key = self._key(conn.self_id)
        old = None
        with self._conn_lock:
            old = self._conns.get(key)
            self._conns[key] = conn
            if conn.self_id:
                self.self_id = str(conn.self_id)
                self.force_self_id = str(conn.self_id)
        # 只有"同一个账号重连"才关旧连接；不同账号之间绝不动对方
        if old is not None and old is not conn:
            try:
                old.close()
            except Exception:
                pass
            log.info("账号 %s 重连，已替换旧连接", key)
        self._conn_event.set()
        log.info("OneBot 连接就绪 self_id=%s（当前在线 %d 个账号）",
                 self.self_id, self.account_count())
        self._safe_call(self.on_connect, self.self_id, "on_connect")

    def detach(self, conn: WSConnection | None = None) -> None:
        """注销连接。传 conn 只摘掉那一条；不传则摘掉全部。"""
        removed: list[str] = []
        with self._conn_lock:
            if conn is None:
                removed = list(self._conns.keys())
                self._conns.clear()
            else:
                for k, c in list(self._conns.items()):
                    if c is conn:
                        del self._conns[k]
                        removed.append(k)
                if not removed:
                    return
            if not self._conns:
                self._conn_event.clear()
                self.self_id = ""
            else:
                # 掉了一个还有别的，把 UI 展示的账号切到还活着的一个
                alive = [c.self_id for c in self._conns.values() if c.self_id]
                self.self_id = alive[0] if alive else ""

        # 只失败"发往这些连接"的请求，别把别的账号的请求也打断
        dead = set(removed)
        with self._pending_lock:
            waiting = [(e, s) for e, s in self._pending.items()
                       if s.get("key") is not None and s["key"] in dead]
            for e, _ in waiting:
                self._pending.pop(e, None)
        for _, slot in waiting:
            slot["error"] = OneBotError("连接已断开")
            slot["event"].set()

        for k in removed:
            log.warning("OneBot 连接断开 self_id=%s（剩余 %d 个账号）", k, self.account_count())
            self._safe_call(self.on_disconnect, k, "on_disconnect")

    def _safe_call(self, fn, arg, name: str) -> None:
        if fn is None:
            return
        try:
            try:
                fn(arg)
            except TypeError:
                fn()
        except Exception:
            log.exception("%s 回调异常", name)

    def connected(self, self_id: str | None = None) -> bool:
        if self.force_connected:
            if self_id is None:
                return True
            return str(self_id) == str(self.force_self_id or self_id)
        with self._conn_lock:
            if self_id is None:
                return any(c.alive for c in self._conns.values())
            c = self._conns.get(self._key(self_id))
            return c is not None and c.alive

    def wait_connected(self, timeout: float = 0) -> bool:
        return self._conn_event.wait(timeout or 0.001)

    def account_count(self) -> int:
        with self._conn_lock:
            return sum(1 for c in self._conns.values() if c.alive)

    def accounts(self) -> list[dict]:
        """当前在线的账号列表，供 UI 展示与补扫遍历。"""
        if self.force_connected:
            sid = str(self.force_self_id or self.force_self_id or "10001")
            return [{"self_id": sid, "nickname": self._nicknames.get(sid, self.nickname),
                     "addr": "mock", "alive": True}]
        out = []
        with self._conn_lock:
            items = list(self._conns.items())
        for key, c in items:
            if not c.alive:
                continue
            sid = str(c.self_id or "")
            out.append({
                "self_id": sid,
                "nickname": self._nicknames.get(sid, ""),
                "addr": f"{c.addr[0]}:{c.addr[1]}" if isinstance(c.addr, tuple) else str(c.addr),
                "alive": True,
            })
        return out

    def set_nickname(self, self_id, nickname: str) -> None:
        if self_id and nickname:
            self._nicknames[str(self_id)] = nickname
            self.nickname = nickname

    # ---------- 收包入口 ----------

    def handle_text(self, conn: WSConnection, text: str) -> None:
        try:
            data = json.loads(text)
        except Exception:
            log.warning("收到非 JSON 帧: %.200s", text)
            return
        if not isinstance(data, dict):
            return

        # 响应帧
        if "echo" in data and ("retcode" in data or "status" in data or "data" in data):
            echo = str(data.get("echo"))
            with self._pending_lock:
                slot = self._pending.pop(echo, None)
            if slot is None:
                return
            slot["resp"] = data
            slot["event"].set()
            return

        # 事件帧
        if "post_type" in data:
            self._emit_event(data, str(conn.self_id or ""))
            return

        # 心跳响应等
        if data.get("post_type") is None and "interval" in data:
            return

    def _emit_event(self, event: dict, self_id: str) -> None:
        handler = self.event_handler
        if handler is None:
            return
        if self._handler_arity is None:
            try:
                self._handler_arity = len(inspect.signature(handler).parameters)
            except (TypeError, ValueError):
                self._handler_arity = 2
        try:
            if self._handler_arity >= 2:
                handler(event, self_id)
            else:
                handler(event)
        except Exception:
            log.exception("事件处理异常")

    # ---------- 调用 ----------

    # 只读白名单：本软件只"看"不"说"。任何会往群里发东西的接口一律拒绝，
    # 防止将来误加功能导致 QQ 号在群里说话（甚至被举报封号）。
    _WRITE_ACTIONS = (
        "send_msg", "send_group_msg", "send_private_msg", "send_group_forward_msg",
        "send_private_forward_msg", "send_forward_msg", "send_like", "set_group_kick",
        "set_group_ban", "set_group_whole_ban", "set_group_admin", "set_group_card",
        "set_group_name", "set_group_leave", "set_group_special_title",
        "set_friend_add_request", "set_group_add_request", "delete_msg",
        "upload_group_file", "upload_private_file", "delete_group_file",
        "create_group_file_folder", "delete_group_folder", "move_group_file",
        "rename_group_file", "trans_group_file", "set_restart", "set_online_status",
    )

    def _assert_readonly(self, action: str) -> None:
        if action in self._WRITE_ACTIONS or action.startswith("send_"):
            raise OneBotError(
                f"已拒绝写操作 {action}：本软件只下载文件，绝不向群里发消息"
                "（见 onebot.py 的只读白名单）"
            )

    def call(self, action: str, params: dict | None = None, timeout: float | None = None,
             self_id: str | None = None) -> dict:
        self._assert_readonly(action)
        timeout = timeout or self.timeout
        params = params or {}

        conn = self._current_conn(self_id)
        if conn is not None:
            return self._call_ws(conn, action, params, timeout)
        if self._http_api:
            return self._call_http(action, params, timeout)
        if self_id:
            raise OneBotError(f"账号 {self_id} 未连接")
        raise OneBotError("OneBot 未连接（既无反向 WS，也未配置 HTTP API）")

    def call_async(self, action: str, params: dict | None = None) -> threading.Thread:
        def runner():
            try:
                self.call(action, params)
            except Exception as exc:
                log.warning("异步调用 %s 失败: %s", action, exc)

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        return t

    def _current_conn(self, self_id: str | None = None) -> WSConnection | None:
        with self._conn_lock:
            if self_id:
                c = self._conns.get(self._key(self_id))
                return c if (c is not None and c.alive) else None
            for c in self._conns.values():
                if c.alive:
                    return c
            return None

    def _call_ws(self, conn: WSConnection, action: str, params: dict, timeout: float) -> dict:
        echo = f"qqark_{next(self._echo)}"
        slot = {"event": threading.Event(), "resp": None, "error": None,
                "key": self._key(conn.self_id)}
        with self._pending_lock:
            self._pending[echo] = slot
        try:
            conn.send_json({"action": action, "params": params, "echo": echo})
        except WSClosed as exc:
            with self._pending_lock:
                self._pending.pop(echo, None)
            raise OneBotError(f"发送失败: {exc}") from exc

        if not slot["event"].wait(timeout):
            with self._pending_lock:
                self._pending.pop(echo, None)
            raise OneBotError(f"调用 {action} 超时（{timeout}s）")
        if slot["error"]:
            raise slot["error"]

        resp = slot["resp"] or {}
        retcode = resp.get("retcode", 0)
        if retcode not in (0, 1, None):
            raise OneBotError(f"{action} 失败 retcode={retcode} msg={resp.get('message') or resp.get('wording') or ''}")
        return resp.get("data") or {}

    def _call_http(self, action: str, params: dict, timeout: float) -> dict:
        url = f"{self._http_api}/{action}"
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._http_token:
            req.add_header("Authorization", f"Bearer {self._http_token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise OneBotError(f"HTTP {exc.code} {action}") from exc
        except Exception as exc:
            raise OneBotError(f"HTTP 调用 {action} 失败: {exc}") from exc
        if data.get("retcode", 0) not in (0, 1):
            raise OneBotError(f"{action} 失败 retcode={data.get('retcode')} msg={data.get('message')}")
        return data.get("data") or {}

    # ---------- 便捷封装 ----------

    def get_group_list(self, self_id: str | None = None) -> list[dict]:
        data = self.call("get_group_list", {}, self_id=self_id)
        return data if isinstance(data, list) else []

    def get_group_info(self, group_id, self_id: str | None = None) -> dict:
        return self.call("get_group_info", {"group_id": str(group_id)}, self_id=self_id)

    # 列目录时一次要多少个文件。
    #
    # 为什么必须显式传：NapCat 的 get_group_root_files 的 file_count 参数
    # **默认只有 50**（napcat.mjs 里 `file_count: p.Union(..., { default: 50 })`），
    # 而它的内部翻页循环在 `r.length === n.fileCount` 时就 break —— 也就是说
    # 不传这个参数，每个目录只会回来前 50 个文件，第 51 个以后根本看不见。
    # 后果非常隐蔽：补扫会漏文件；更糟的是 find_group_file 找不到某些文件，
    # 于是把它们判成「群里已无此文件」（gone）——用户真正想要的文件永远下不来。
    # QQ 群文件上限是 10000，这里取一个足够大又不会太慢的值。
    LIST_FILE_COUNT = 1000

    def get_group_root_files(self, group_id, self_id: str | None = None,
                             file_count: int | None = None) -> dict:
        return self.call("get_group_root_files",
                         {"group_id": str(group_id),
                          "file_count": int(file_count or self.LIST_FILE_COUNT)},
                         timeout=60, self_id=self_id)

    def get_group_files_by_folder(self, group_id, folder_id: str,
                                  self_id: str | None = None,
                                  file_count: int | None = None) -> dict:
        return self.call("get_group_files_by_folder",
                         {"group_id": str(group_id), "folder_id": folder_id,
                          "file_count": int(file_count or self.LIST_FILE_COUNT)},
                         timeout=60, self_id=self_id)

    def get_group_file_url(self, group_id, file_id: str, busid=0,
                           self_id: str | None = None) -> str:
        data = self.call("get_group_file_url",
                         {"group_id": str(group_id), "file_id": file_id, "busid": int(busid or 0)},
                         self_id=self_id)
        return data.get("url") or ""

    def _list_group_files(self, group_id, self_id: str | None = None,
                          max_depth: int = 3) -> tuple[list[dict], bool]:
        """把整棵群目录树摊平成文件列表，结果缓存 60 秒。

        为什么要摊平+缓存：一批老文件换不到链接时，每个任务都会来问一次
        "这文件还在不在"。原来每次查找都从根目录重新走一遍整棵树，实测
        74 个失败文件 × 3 次重试 = 222 次全树遍历，把 NapCat 拖到列目录超时
        （超时又会误判成"没读到目录"，进而重试更多次，越滚越糟）。
        摊平一次、60 秒内所有人共用，中断这个正反馈。

        返回 (文件列表, 是否有目录读取失败)。第二项必须是精确的：调用方
        靠它区分"文件真没了"和"这次没读全，别下结论"。
        """
        key = (str(group_id), str(self_id or ""))
        now = time.time()
        cached = self._list_cache.get(key)
        if cached and now - cached[0] < self._list_cache_ttl:
            return list(cached[1]), False

        visited: set[str] = set()
        collected: list[dict] = []
        errors: list[str] = []

        def walk(folder_id: str | None, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                if folder_id is None:
                    data = self.get_group_root_files(group_id, self_id=self_id)
                else:
                    if folder_id in visited:
                        return
                    visited.add(folder_id)
                    data = self.get_group_files_by_folder(group_id, folder_id, self_id=self_id)
            except OneBotError as exc:
                errors.append(f"{folder_id or 'root'}: {exc}")
                log.warning("列群文件时读目录失败 %s/%s: %s", group_id, folder_id, exc)
                return
            for f in data.get("files") or []:
                collected.append(f)
            for sub in data.get("folders") or []:
                sid = sub.get("folder_id") or sub.get("id") or ""
                if sid:
                    walk(sid, depth + 1)

        walk(None, 1)
        self._scan_errors = list(errors)
        # 只有完整读下来才缓存：读了一半的树缓存起来会让"文件不存在"
        # 这个判断在 60 秒内一直是错的。
        if not errors:
            self._list_cache[key] = (now, collected)
        return collected, bool(errors)

    def invalidate_list_cache(self, group_id=None) -> None:
        """外部改动群文件后调用（比如刚上传完），让下一次查找重新列目录。"""
        if group_id is None:
            self._list_cache.clear()
            return
        for key in [k for k in self._list_cache if k[0] == str(group_id)]:
            self._list_cache.pop(key, None)

    def find_group_file(self, group_id, file_name: str, size: int = 0,
                        self_id: str | None = None, max_depth: int = 3) -> dict | None:
        """按 文件名（+大小）现查一遍群文件树，返回最新鲜的 {file_id, busid, ...}。

        为什么需要：NapCat 每次列举群文件都会重新生成 file_id，库里存的
        file_id 隔一段时间就"过期"了，拿它换链接会收到
        `retcode=1200 real fileUUID not found!`（实测）。这时唯一的办法
        就是重新列举一次群目录，拿到当前有效的 file_id。
        """
        want_name = (file_name or "").strip()
        if not want_name:
            return None

        def norm(s: str) -> str:
            return (s or "").strip().casefold()

        # 串行化：并发 worker 同时遍历同一棵群目录树时，NapCat 会排队并可能
        # 超时，表现就是"明明存在的文件查不到"（实测 3 个文件这么被误判）。
        with self._list_lock:
            files, _ = self._list_group_files(group_id, self_id=self_id, max_depth=max_depth)

        fallback = None
        target = norm(want_name)
        for f in files:
            name = f.get("file_name") or f.get("name") or ""
            fid = f.get("file_id") or f.get("id") or ""
            if not fid or norm(name) != target:
                continue
            fs = int(f.get("file_size") or f.get("size") or 0)
            if not size or not fs or fs == size:
                return f
            if fallback is None:
                fallback = f
        return fallback

    def get_file(self, file_id: str, self_id: str | None = None) -> dict:
        return self.call("get_file", {"file_id": file_id}, self_id=self_id)

    def get_group_msg_history(self, group_id, count: int = 50,
                              self_id: str | None = None) -> list[dict]:
        data = self.call("get_group_msg_history", {"group_id": str(group_id), "count": count},
                         self_id=self_id)
        return data.get("messages", []) if isinstance(data, dict) else []

    def get_login_info(self, self_id: str | None = None) -> dict:
        return self.call("get_login_info", {}, self_id=self_id)

    def get_status(self, self_id: str | None = None) -> dict:
        return self.call("get_status", {}, self_id=self_id)
