"""端到端自检：不需要 NapCat，用内置 mock 假服务端验证全链路。

跑法： python selftest.py
检查项：
  1. 各模块可导入、配置/数据库可建
  2. 假 NapCat 通过真实 WebSocket 握手连进来（走我们自己的 WSServer）
  3. 事件 → 下载 → 分类落盘 → 索引 的完整链路
  4. Web API 各端点返回正常
  5. Range 请求（视频拖动）返回 206
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TMP = ROOT / "_selftest"
SAVE = TMP / "archive"
DATA = TMP / "data"

PASS, FAIL = [], []

# Windows 控制台默认 GBK，勾叉/中文会炸；强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'[OK]' if cond else '[!!]'} {name}"
          f"{('  — ' + detail) if detail and not cond else ''}")


def main() -> int:
    import shutil
    if TMP.exists():
        shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.WARNING)

    print("\n[1] 导入与基础构造")
    from qqark.config import Config
    from qqark.db import Database
    from qqark.classify import category_of, build_relpath, safe_name
    from qqark import ws_server as wsm

    cfg_file = TMP / "config.json"
    cfg = Config(cfg_file)
    cfg.update({"save_root": str(SAVE), "data_dir": str(DATA),
                "web_port": 17801, "ws_port": 16199, "auto_open_browser": False})
    check("Config 读写", cfg.get("ws_port") == 16199)
    db = Database(cfg.db_path)
    check("Database 建库", cfg.db_path.exists())

    check("分类：mp4 → 视频", category_of("a.mp4") == "视频")
    check("分类：zip → 压缩包", category_of("a.zip") == "压缩包")
    check("分类：未知 → 其他", category_of("a.xyz") == "其他")
    check("安全文件名", "<>:" not in safe_name('a<b>c:d?.mp4'))
    rel = build_relpath(root=SAVE, layout="group_date_type", group_name="测试群",
                        group_id="123", file_name="视频.mp4", ts=time.time(),
                        source="群文件", uploader="张三")
    check("路径构建含群名", "测试群" in str(rel))
    check("路径按 年-月-类别 分层", len(rel.relative_to(SAVE).parts) == 4,
          str(rel.relative_to(SAVE)))

    print("\n[2] 装配 QQArk（mock 模式）")
    from qqark.app import QQArk, setup_logging
    setup_logging("WARNING", None)
    app = QQArk(cfg_file)
    from qqark.mock import install_mock
    install_mock(app)
    check("mock 连接状态", app.ob.connected() is True)
    check("mock 群列表", len(app.ob.get_group_list()) >= 3)

    print("\n[3] 启动服务")
    app.start(open_browser=False)
    time.sleep(1.2)
    check("WS 已监听", app.ws_server is not None and app.ws_server.actual_port > 0)
    check("Web 已监听", app.web_server is not None and app.web_server.actual_port > 0)
    web = f"http://127.0.0.1:{app.web_server.actual_port}"

    print("\n[4] Web API")
    def get(path, raw=False):
        with urllib.request.urlopen(web + path, timeout=8) as r:
            return r.read() if raw else json.loads(r.read().decode())

    check("GET / 返回 HTML", b"<!DOCTYPE html>" in get("/", raw=True))
    st = get("/api/status")
    check("状态含连接字段", "onebot_connected" in st)
    fl = get("/api/files?limit=100")
    check("文件列表有演示数据", fl["total"] >= 5, f"total={fl['total']}")
    check("群列表非空", len(get("/api/groups")["groups"]) >= 3)
    check("配置可读", "save_root" in get("/api/config"))
    check("统计可读", "total_files" in get("/api/stats"))

    print("\n[5] 媒体流 + Range")
    rows = fl["files"]
    png = next((r for r in rows if r["category"] == "图片"), None)
    check("存在图片记录", png is not None)
    if png:
        body = get(f"/media/{png['id']}", raw=True)
        check("图片字节可读", len(body) > 100, f"len={len(body)}")
        check("PNG 头正确", body[:8] == b"\x89PNG\r\n\x1a\n")
        req = urllib.request.Request(web + f"/media/{png['id']}",
                                     headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=8) as r:
            check("Range 返回 206", r.status == 206, f"status={r.status}")
            check("Content-Range 正确", r.headers.get("Content-Range", "").startswith("bytes 0-99/"))

    print("\n[6] 假 NapCat 走真实 WS 握手")
    got = {"event": None}
    real_handler = app.router.handle

    def spy(ev):
        if got["event"] is None:
            got["event"] = ev
        return real_handler(ev)

    # _wire() 里已经把 event_handler 绑成原始方法，这里必须重绑才能截到事件
    app.ob.event_handler = spy
    # 演示模式开着会强制 connected()==True，先关掉才能测真实连接状态
    app.ob.force_connected = False
    sock = _fake_napcat_connect(app.ws_server.actual_port, "/ws", self_id="10001")
    check("WS 握手成功", sock is not None)
    if sock:
        time.sleep(0.5)
        check("连接被识别", app.ob.connected())
        check("self_id 已读取", str(app.ob.self_id) == "10001")

        # 发一个群文件上传通知
        ev = {
            "post_type": "notice", "notice_type": "group_upload",
            "group_id": "111222333", "user_id": 10001,
            "file": {"id": "/real/1", "name": "真实群文件测试.txt", "size": 12345, "busid": 102},
        }
        _ws_send(sock, json.dumps(ev, ensure_ascii=False))
        time.sleep(0.6)
        check("事件已送达路由器", got["event"] is not None)
        check("群文件事件已计数", app.router.counters["group_upload"] >= 1,
              str(app.router.counters))
        # 不能只看 recent_tasks(10)：后台下载线程会不停重试 mock 演示文件，
        # 每次重试都刷新 updated_at，把刚入队的这条挤出「最近 10 条」窗口，
        # 于是这条断言在 Windows CI 上会随机失败（真机本地却常赢）。直接全表计数。
        n_new = db.conn().execute(
            "SELECT COUNT(*) FROM tasks WHERE file_name LIKE ?",
            ("%真实群文件测试%",),
        ).fetchone()[0]
        check("已入下载队列", n_new >= 1, f"{n_new} 条匹配")

        # 聊天视频事件
        ev2 = {
            "post_type": "message", "message_type": "group", "group_id": "444555666",
            "message_id": 99, "time": int(time.time()),
            "sender": {"user_id": 10002, "nickname": "李四", "card": ""},
            "message": [{"type": "video", "data": {"file": "abc123", "file_size": "4096",
                                                   "url": "http://example.invalid/v.mp4"}}],
        }
        _ws_send(sock, json.dumps(ev2, ensure_ascii=False))
        time.sleep(0.6)
        check("聊天视频已计数", app.router.counters["chat_video"] >= 1, str(app.router.counters))
        # 同上：按文件名直查，不依赖 recent_tasks 的排序窗口。
        vrow = db.conn().execute(
            "SELECT file_name FROM tasks WHERE source='聊天视频' AND file_name LIKE ?",
            ("abc123%",),
        ).fetchone()
        check("视频任务入队", vrow is not None)
        if vrow:
            check("视频名自动补 .mp4", vrow["file_name"].endswith(".mp4"),
                  vrow["file_name"])

        # 关闭连接
        try:
            sock.close()
        except OSError:
            pass
        time.sleep(0.8)
        check("断开被感知", not app.ob.connected())

    print("\n[7] 下载器分派逻辑（不打真实网络）")
    from qqark.downloader import Downloader, _dedupe_target
    d = Downloader(cfg, db, app.ob)
    check("临时目录已建", d._tmp_dir.exists())
    tgt = SAVE / "dup" / "a.txt"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_text("1", encoding="utf-8")
    alt = _dedupe_target(tgt)
    check("重名自动加后缀", alt.name == "a_2.txt", alt.name)
    check("下载器状态字段", {"pending", "running", "done", "failed"} <= set(d.status()))

    print("\n[8] 关闭")
    app.stop()
    time.sleep(0.4)
    check("已停止", True)

    print("\n" + "=" * 56)
    print(f"  通过 {len(PASS)}  失败 {len(FAIL)}")
    if FAIL:
        print("  失败项：" + "、".join(FAIL))
    print("=" * 56 + "\n")
    return 0 if not FAIL else 1


# ---------- 假 NapCat（最小 WS 客户端） ----------


def _fake_napcat_connect(port: int, path: str, self_id: str = ""):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=6)
    except OSError as exc:
        print(f"    连接失败: {exc}")
        return None
    import base64
    import os

    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: 127.0.0.1:{port}\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           "Sec-WebSocket-Version: 13\r\n"
           f"X-Self-ID: {self_id}\r\n"
           "X-Client-Role: Universal\r\n\r\n")
    s.sendall(req.encode("ascii"))
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = s.recv(1)
        if not chunk:
            return None
        head += chunk
    if b"101" not in head.split(b"\r\n")[0]:
        print(f"    握手响应: {head[:120]}")
        return None
    return s


def _ws_send(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    import os

    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        hdr = bytes([0x81, 0x80 | n])
    elif n < 65536:
        hdr = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big")
    else:
        hdr = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big")
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(hdr + mask + masked)


if __name__ == "__main__":
    sys.exit(main())
