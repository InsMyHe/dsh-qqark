"""装配层：把各模块拼成一个可运行的守护进程。

启动顺序（有依赖，不能乱改）：
  1. Config / Database     —— 最先，后续都要用
  2. Downloader            —— 先起队列，事件进来就能入队
  3. OneBotClient          —— 只是容器，还没连接
  4. EventRouter / Scanner —— 依赖上面三个
  5. WSServer              —— 开始监听 6200，等 NapCat 反向连上来
  6. WebServer             —— 最后起 UI，此时状态已就绪
"""

from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from pathlib import Path

from .config import Config
from .db import Database
from .downloader import Downloader
from .events import EventRouter
from .lockfile import InstanceLock, LockBusy
from .onebot import OneBotClient, OneBotError
from .scanner import Scanner
from .web import WebServer
from .ws_server import WSConnection, WSServer

log = logging.getLogger("qqark")


class QQArk:
    def __init__(self, config_path: Path | None = None):
        root = Path(__file__).resolve().parent.parent
        self.config_path = Path(config_path) if config_path else root / "config.json"
        self.cfg = Config(self.config_path)
        self.db = Database(self.cfg.db_path)

        self.ob = OneBotClient(timeout=float(self.cfg.get("timeout") or 60))
        self.dl = Downloader(self.cfg, self.db, self.ob)
        self.router = EventRouter(self.cfg, self.db, self.ob, self.dl)
        self.scanner = Scanner(self.cfg, self.db, self.ob, self.dl, self.router)

        self.ws_server: WSServer | None = None
        self.web_server: WebServer | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock: InstanceLock | None = None
        self.launch_abort_reason = ""     # 抢锁/端口失败时写这里，供 WebUI 展示
        self.started_at = 0.0

        self._wire()

    # ---------- 回调接线 ----------

    def _wire(self) -> None:
        self.ob.event_handler = self.router.handle
        self.ob.on_connect = self._on_onebot_connect
        self.ob.on_disconnect = self._on_onebot_disconnect

        api = (self.cfg.get("http_api") or "").strip()
        if api:
            self.ob.set_http(api, self.cfg.get("http_token") or "")

    def _on_onebot_connect(self, self_id: str = "") -> None:
        log.info("NapCat 已接入（账号 %s），开始工作", self_id or "未知")
        # 连上后拉一次群列表，让 UI 立刻有群可选
        threading.Thread(target=self._bootstrap_groups, args=(self_id,),
                         name="boot-groups", daemon=True).start()

    def _on_onebot_disconnect(self, self_id: str = "") -> None:
        log.warning("账号 %s 断开，等待重连（补扫器会在重连后补齐群文件）",
                    self_id or "未知")

    def _bootstrap_groups(self, self_id: str = "") -> None:
        if not self_id:
            ids = [a["self_id"] for a in self.ob.accounts()]
            self_id = ids[0] if ids else ""
        # 昵称只是锦上添花：某些 NapCat 版本的 get_login_info 会一直不返回，
        # 绝不能让它挡住群列表刷新（超时 8 秒就放弃）。
        self._learn_nickname(self_id)
        # 群列表才是关键，无论上面成没成功都要刷
        try:
            self.router.refresh_groups(self_id=self_id or None)
        except OneBotError as exc:
            log.warning("初始化群列表失败: %s", exc)
        except Exception:
            log.exception("初始化流程异常")

    def _learn_nickname(self, self_id: str) -> None:
        """取账号昵称。先试 OneBot，超时/为空则退回 NapCat WebUI。"""
        nick = ""
        try:
            info = self.ob.call("get_login_info", {}, 6, self_id=self_id or None)
            nick = str((info or {}).get("nickname") or (info or {}).get("nick") or "")
        except Exception as exc:
            log.debug("OneBot get_login_info 不可用（忽略）: %s", exc)

        if not nick:
            # NapCat 9.9.33 上 get_login_info 不返回，但 WebUI 一定有昵称
            try:
                from .napcat import find_napcat_dirs, query_login_info_via_webui
                got = query_login_info_via_webui(find_napcat_dirs(), timeout=3)
                if got:
                    nick = got.get("nick") or ""
                    if not self_id:
                        self_id = got.get("uin") or ""
            except Exception as exc:
                log.debug("从 NapCat WebUI 取昵称失败（忽略）: %s", exc)

        if self_id:
            if nick:
                self.ob.set_nickname(self_id, nick)
            try:
                self.db.touch_account(self_id, nick)
            except Exception:
                pass

    # ---------- 启停 ----------

    def start(self, open_browser: bool | None = None) -> None:
        # 抢锁必须在做任何有副作用的事（reset_stuck_tasks / 建目录 / 下任务）之前。
        # 抢不到就直接抛：宁可启动失败，也不能两个实例同时啃一个库。
        self._acquire_lock()
        try:
            self._prepare_dirs()
            self.db.reset_stuck_tasks()

            # 先绑 WS 再起 worker：端口绑不上就该整体失败退出，
            # 不能先把下载线程点着再半路 SystemExit。
            self._start_ws()
            self.dl.start()
            self.scanner.start()
            self._start_web()
        except BaseException:
            # 启动半路失败也要把锁放开，否则用户重开一次就永远"已有实例在运行"
            self._release_lock()
            raise

        self.started_at = time.time()
        self._ready.set()

        if open_browser is None:
            open_browser = bool(self.cfg.get("auto_open_browser"))
        if open_browser and self.web_server:
            threading.Timer(0.8, lambda: _safe_open(self.web_server.url)).start()

    def _acquire_lock(self) -> None:
        """拿数据目录的单实例锁。拿不到抛 LockBusy，由 main.py 友好提示后退出。"""
        lock = InstanceLock(self.cfg.data_dir)
        try:
            lock.acquire()
        except LockBusy as exc:
            self.launch_abort_reason = str(exc)
            raise
        self._lock = lock
        log.info("已获得单实例锁 %s", lock.path)

    def _release_lock(self) -> None:
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:
                pass
            self._lock = None

    def _prepare_dirs(self) -> None:
        try:
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            (self.cfg.data_dir / "thumbs").mkdir(parents=True, exist_ok=True)
            (self.cfg.data_dir / "tmp").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.error("数据目录不可写: %s (%s)", self.cfg.data_dir, exc)
        try:
            self.cfg.save_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.error("保存目录不可用: %s (%s) —— 仍会继续运行，但下载可能失败",
                      self.cfg.save_root, exc)

    def _start_ws(self) -> None:
        host = self.cfg.get("ws_host") or "127.0.0.1"
        port = int(self.cfg.get("ws_port") or 6200)
        want_path = self.cfg.get("ws_path") or "/ws"

        def on_open(conn: WSConnection) -> bool:
            path = (conn.path or "").split("?")[0]
            if want_path and path not in (want_path, "/", ""):
                log.warning("拒绝连接：路径不匹配 %s（期望 %s）", path, want_path)
                return False
            log.info("NapCat 连接建立 self_id=%s role=%s path=%s",
                     conn.self_id, conn.role, path)
            self.ob.attach(conn)
            return True

        def on_text(conn: WSConnection, text: str) -> None:
            self.ob.handle_text(conn, text)

        def on_close(conn: WSConnection, reason: str) -> None:
            log.info("NapCat 连接关闭: %s", reason)
            self.ob.detach(conn)

        # 这里曾经是"端口被占就顺延到 6201 并继续启动"。
        # 那个设计是错的：NapCat 里配的是固定 6200 的反向 WS，顺延后的实例
        # 永远收不到连接，却照样从共享 SQLite 里领任务，换取下载链接时
        # 一律抛 `账号 xxx 未连接`，把 20 个真实文件误标成 failed。
        # 现在改为：绑不上就直接让启动失败，宁可不启动也不静默带病运行。
        try:
            self.ws_server = WSServer(host, port, on_open=on_open,
                                      on_text=on_text, on_close=on_close)
            self.ws_server.start()
        except OSError as exc:
            self.ws_server = None
            msg = (f"WS 端口 {host}:{port} 绑定失败（{exc}）。"
                   f"多半是另一个归档器实例还活着，或别的程序占用了该端口。"
                   f"请先关掉它再启动。")
            self.launch_abort_reason = msg
            log.error(msg)
            raise SystemExit(2)
        log.info("等待 NapCat 连接：ws://%s:%s%s", host, port, want_path)

    def _start_web(self) -> None:
        for attempt in range(20):
            try:
                self.web_server = WebServer(self.cfg, self.db, self)
                self.web_server.start()
                return
            except OSError as exc:
                # 端口被占（3600 常见），顺延到下一个端口
                cur = int(self.cfg.get("web_port") or 7801)
                self.cfg.set("web_port", cur + 1)
                log.warning("Web 端口 %s 被占用，改用 %s", exc, cur + 1)
        log.error("Web UI 启动失败")

    def request_stop(self) -> None:
        """请求退出。

        给 HTTP 的 /api/shutdown 用：只置标志，真正的清理仍由主循环走 stop()，
        这样不会在请求线程里拆掉自己脚下的 web 服务器。
        """
        log.info("收到退出请求")
        self._stop.set()

    def should_stop(self) -> bool:
        """主循环用它判断是否该退出。"""
        return self._stop.is_set()

    def stop(self) -> None:
        log.info("正在退出…")
        self._stop.set()
        try:
            if self.web_server:
                self.web_server.stop()
        except Exception:
            pass
        try:
            self.scanner.stop()
        except Exception:
            pass
        try:
            self.dl.stop()
        except Exception:
            pass
        try:
            if self.ws_server:
                self.ws_server.stop()
        except Exception:
            pass
        try:
            self.cfg.save()
        except Exception:
            pass
        # 锁最后放：前面几步可能还在写库，提前放会让另一个实例挤进来。
        self._release_lock()
        log.info("已退出")

    # ---------- 前台运行 ----------

    def wait(self) -> None:
        """阻塞到 Ctrl+C。"""
        self._ready.wait(timeout=30)
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def run_forever(self) -> None:
        self.start()
        self.wait()

    # ---------- 便捷信息 ----------

    def info(self) -> dict:
        return {
            "version": "1.0.0",
            "config": str(self.config_path),
            "save_root": str(self.cfg.save_root),
            "web_url": self.web_server.url if self.web_server else "",
            "ws": f"ws://{self.cfg.get('ws_host')}:{self.cfg.get('ws_port')}{self.cfg.get('ws_path')}",
            "connected": self.ob.connected(),
            "self_id": self.ob.self_id,
            "groups": len(self.db.list_groups()),
            "files": self.db.stats()["total_files"],
        }


def _safe_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """统一日志：控制台带颜色，文件全量（便于排障）。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)-16s %(message)s",
                            datefmt="%H:%M:%S")

    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    ch.setFormatter(fmt)
    try:
        import colorama  # 可选
        colorama.just_fix_windows_console()
    except Exception:
        pass
    root.addHandler(ch)

    if log_file:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-5s %(threadName)-10s %(name)-16s %(message)s"))
            root.addHandler(fh)
        except OSError:
            pass

    logging.getLogger("qqark").setLevel(logging.DEBUG)
    # 第三方库降噪
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
