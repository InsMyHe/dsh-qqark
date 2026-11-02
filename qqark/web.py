"""本地 Web 媒体库。

单个 ThreadingHTTPServer 提供：
  GET /                         前端单页（内联 HTML/CSS/JS，无外部依赖）
  GET /api/stats                统计
  GET /api/files?...            列表（筛选/分页）
  GET /api/groups               群列表 + 监听开关
  GET /api/config               当前配置
  POST /api/config              改配置（白名单字段）
  GET /api/tasks                最近任务/进度
  POST /api/scan                手动触发补扫
  POST /api/retry               重试失败任务
  POST /api/group_enable        开/关某群抓取
  POST /api/open_folder         在资源管理器打开文件所在目录
  GET /media/<id>               流式返回文件（支持 Range，视频可拖动）
  GET /thumb/<id>               缩略图（有 Pillow 用 Pillow，没有就回原图）
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import posixpath
import re
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .db import Database

log = logging.getLogger("qqark.web")

from .webui import INDEX_HTML  # noqa: E402  （前端资源单独文件，便于维护）


class WebServer:
    def __init__(self, cfg: Config, db: Database, app):
        self.cfg = cfg
        self.db = db
        self.app = app   # QQArk 实例，用于取下载器/补扫器状态
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.actual_port = int(cfg.get("web_port") or 7801)

    def start(self) -> None:
        handler = _make_handler(self)
        host = self.cfg.get("web_host") or "127.0.0.1"
        port = int(self.cfg.get("web_port") or 7801)
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.actual_port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="web-ui", daemon=True)
        self.thread.start()
        log.info("媒体库已启动: http://%s:%s", host, self.actual_port)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=3)

    @property
    def url(self) -> str:
        return f"http://{self.cfg.get('web_host') or '127.0.0.1'}:{self.actual_port}/"


def _make_handler(server: WebServer):
    db = server.db
    cfg = server.cfg
    app = server.app

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "QQArk/1.0"

        # ---------- 基础 ----------

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def handle_one_request(self):
            """吞掉浏览器主动断连引发的异常，免得刷一堆无意义堆栈。

            WinError 10053/10054 在单页刷新、拖视频进度条时非常常见，属正常行为。
            """
            try:
                super().handle_one_request()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                self.close_connection = True

        def _send_json(self, obj, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _send_text(self, text: str, ctype: str = "text/html; charset=utf-8", code: int = 200):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _read_json(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return {}

        # ---------- 路由 ----------

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                return self._send_text(INDEX_HTML)
            if path == "/api/stats":
                return self._send_json(self._stats())
            if path == "/api/files":
                return self._send_json(self._files(qs))
            if path == "/api/groups":
                return self._send_json({"groups": self._groups()})
            if path == "/api/config":
                return self._send_json(cfg.as_dict())
            if path == "/api/tasks":
                return self._send_json({
                    "downloader": app.dl.status(),
                    "scanner": app.scanner.status(),
                    "tasks": db.recent_tasks(40),
                })
            if path == "/api/status":
                return self._send_json(self._status())
            if path == "/api/accounts":
                return self._send_json({
                    "connected": app.ob.accounts(),
                    "known": db.list_accounts(),
                })
            m = re.fullmatch(r"/media/(\d+)", path)
            if m:
                return self._stream_file(int(m.group(1)))
            m = re.fullmatch(r"/thumb/(\d+)", path)
            if m:
                return self._thumb(int(m.group(1)))
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            body = self._read_json()

            if path == "/api/config":
                allowed = {
                    "watch_groups", "grab_group_files", "grab_chat_video", "grab_chat_file",
                    "grab_images", "grab_audio", "scan_enabled", "scan_interval_min",
                    "concurrency", "min_size", "max_size_mb", "dedupe", "skip_existing",
                    "layout", "auto_open_browser", "http_api", "http_token", "save_root",
                }
                patch = {k: v for k, v in body.items() if k in allowed}
                if "layout" in patch and patch["layout"] not in (
                        "group_date_type", "group_type", "date_type", "type_only"):
                    patch.pop("layout")
                # 保存位置：空值或纯空白直接丢弃，别让配置变成写不进去的空路径
                if "save_root" in patch:
                    sr = str(patch["save_root"] or "").strip()
                    patch["save_root"] = sr
                    if not sr:
                        patch.pop("save_root")
                if "watch_groups" in patch and isinstance(patch["watch_groups"], str):
                    patch["watch_groups"] = [
                        s.strip() for s in re.split(r"[,\s]+", patch["watch_groups"]) if s.strip()
                    ]
                cfg.update(patch)
                return self._send_json({"ok": True, "config": cfg.as_dict()})

            if path == "/api/scan":
                app.scanner.trigger()
                return self._send_json({"ok": True, "msg": "补扫已触发"})

            if path == "/api/retry":
                # 只重试 failed。`gone` 是「群里已经确认没有了」的终态，
                # 重试它除了重新遍历一遍群目录树之外不会有任何结果。
                rows = db.conn().execute("SELECT id FROM tasks WHERE state='failed'").fetchall()
                for r in rows:
                    db.retry_task(r["id"])
                app.dl.notify()
                return self._send_json({"ok": True, "retried": len(rows)})

            if path == "/api/group_enable":
                gid = str(body.get("group_id") or "")
                enabled = bool(body.get("enabled"))
                if gid:
                    db.set_group_enabled(gid, enabled)
                return self._send_json({"ok": True})

            if path == "/api/refresh_groups":
                sid = str(body.get("self_id") or "") or None
                groups = app.router.refresh_groups(self_id=sid)
                return self._send_json({"ok": True, "groups": groups})



            if path == "/api/open_folder":
                fid = body.get("id")
                row = db.conn().execute("SELECT save_path FROM files WHERE id=?", (fid,)).fetchone()
                if not row:
                    return self._send_json({"error": "记录不存在"}, 404)
                p = Path(row["save_path"])
                if not p.exists():
                    return self._send_json({"error": "文件已不存在"}, 404)
                _open_in_explorer(p)
                return self._send_json({"ok": True})

            if path == "/api/delete":
                fid = body.get("id")
                delete_disk = bool(body.get("delete_disk"))
                row = db.delete_file_row(int(fid or 0))
                if not row:
                    return self._send_json({"error": "记录不存在"}, 404)
                removed = False
                if delete_disk:
                    p = Path(row.get("save_path") or "")
                    try:
                        if p.is_file():
                            p.unlink()
                            removed = True
                    except OSError as exc:
                        return self._send_json({"ok": True, "file_removed": False,
                                                "error": f"记录已删，文件删除失败: {exc}"})
                return self._send_json({"ok": True, "file_removed": removed})

            if path == "/api/shutdown":
                # 先回包再退出：延迟 0.3 秒再置停止标志，保证这次响应能发出去。
                # 只置标志，真正清理交给 main.py 的主循环，避免在请求线程里
                # 把正在处理本请求的 web 服务器自己拆掉。
                threading.Timer(0.3, app.request_stop).start()
                return self._send_json({"ok": True, "msg": "正在退出"})

            return self._send_json({"error": "not found"}, 404)

        # ---------- 数据 ----------

        def _status(self):
            accts = app.ob.accounts()
            first = accts[0] if accts else {}
            return {
                "onebot_connected": app.ob.connected(),
                "self_id": app.ob.self_id,
                "nickname": app.ob.nickname or first.get("nickname", ""),
                "account_count": len(accts),
                "accounts": accts,
                "ws_port": app.ws_server.actual_port if app.ws_server else None,
                "web_port": server.actual_port,
                "save_root": str(cfg.save_root),
                "save_root_ok": _dir_writable(cfg.save_root),
                "counters": dict(app.router.counters),
            }

        def _stats(self):
            s = db.stats()
            s["status"] = self._status()
            s["downloader"] = app.dl.status()
            return s

        def _groups(self):
            groups = db.list_groups()
            enabled = db.group_enabled_map()
            for g in groups:
                g["enabled"] = enabled.get(g["group_id"], True)
                g["last_scan"] = g.get("last_scan") or 0
            return groups

        def _files(self, qs):
            def get(k, d=""):
                v = qs.get(k)
                return v[0] if v else d

            try:
                limit = int(get("limit", "60"))
                offset = int(get("offset", "0"))
            except ValueError:
                limit, offset = 60, 0

            rows, total = db.query_files(
                q=get("q", ""),
                group_id=get("group_id", ""),
                category=get("category", ""),
                source=get("source", ""),
                media_only=get("media_only", "") in ("1", "true"),
                sort=get("sort", "new"),
                limit=limit,
                offset=offset,
            )
            return {"files": rows, "total": total, "limit": limit, "offset": offset}

        # ---------- 文件流 ----------

        def _lookup(self, fid: int):
            row = db.conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
            if not row:
                return None
            p = Path(row["save_path"] or "")
            if not p.is_file():
                return None
            return row, p

        def _stream_file(self, fid: int):
            found = self._lookup(fid)
            if not found:
                return self._send_json({"error": "文件不存在"}, 404)
            _row, p = found
            self._serve_range(p, download=False)

        def _thumb(self, fid: int):
            found = self._lookup(fid)
            if not found:
                return self._send_json({"error": "文件不存在"}, 404)
            row, p = found
            cache = cfg.data_dir / "thumbs" / f"{row['id']}.jpg"
            if not cache.exists():
                _make_thumb(p, cache, row["category"])
            if cache.exists():
                return self._serve_range(cache, download=False)
            # 没有缩略图能力时回原文件（前端会优雅降级）
            return self._serve_range(p, download=False)

        def _serve_range(self, path: Path, download: bool = False):
            try:
                stat = path.stat()
            except OSError:
                return self._send_json({"error": "文件不可读"}, 404)

            size = stat.st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            # Windows 上 .ts/.mkv 的注册表映射可能给错，强制纠正常见视频类型
            ext = path.suffix.lower()
            if ext in (".mp4", ".m4v"):
                ctype = "video/mp4"
            elif ext == ".mkv":
                ctype = "video/x-matroska"
            elif ext == ".webm":
                ctype = "video/webm"
            elif ext in (".mp3",):
                ctype = "audio/mpeg"
            elif ext == ".flac":
                ctype = "audio/flac"

            start, end = 0, size - 1
            status = 200
            range_header = self.headers.get("Range")
            if range_header:
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if m:
                    s, e = m.group(1), m.group(2)
                    if s:
                        start = int(s)
                        end = int(e) if e else size - 1
                    elif e:  # bytes=-N 后缀范围
                        start = max(0, size - int(e))
                        end = size - 1
                    if start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    end = min(end, size - 1)
                    status = 206

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download:
                fn = urllib.parse.quote(path.name)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{fn}")
            self.end_headers()

            try:
                with open(path, "rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        block = fh.read(min(262144, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 用户拖动进度条/关页面会主动断连，正常（WinError 10053 = ConnectionAbortedError）

    return Handler


# ---------- 辅助 ----------


def _dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".qqark_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _open_in_explorer(p: Path) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception:
        log.exception("打开目录失败: %s", p)


def _make_thumb(src: Path, dst: Path, category: str, max_side: int = 480) -> None:
    """生成缩略图。缺 Pillow 或失败时静默跳过（前端回退到原文件/图标）。"""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        from PIL import Image  # 可选依赖
    except Exception:
        return

    try:
        if category == "图片":
            img = Image.open(src)
            img.draft("RGB", (max_side, max_side))
            img.thumbnail((max_side, max_side))
            img.convert("RGB").save(dst, "JPEG", quality=82)
            return
        if category == "视频":
            ffmpeg = _find_ffmpeg()
            if not ffmpeg:
                return
            tmp = dst.with_suffix(".raw.jpg")
            subprocess.run(
                [ffmpeg, "-y", "-ss", "00:00:02", "-i", str(src), "-frames:v", "1",
                 "-vf", f"scale='min({max_side},iw)':-2", str(tmp)],
                capture_output=True, timeout=25,
            )
            if tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, dst)
    except Exception:
        log.debug("缩略图生成失败: %s", src)


def _find_ffmpeg() -> str | None:
    from shutil import which

    p = which("ffmpeg")
    if p:
        return p
    # 常见内置位置
    for cand in (
        Path(__file__).parent / "bin" / "ffmpeg.exe",
        Path(__file__).parent.parent / "bin" / "ffmpeg.exe",
    ):
        if cand.is_file():
            return str(cand)
    return None
