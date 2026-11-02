"""演示模式：不接 NapCat 也能看界面长什么样。

`python main.py --mock` 会：
  1. 伪造 OneBot 连接状态（显示"已连接"）
  2. 往数据库塞一批示例群和文件记录
  3. 在保存目录里真的生成几个小文件，让预览/播放能点开

用途：验证界面、布局、缩略图、播放器是否正常，不碰 QQ。
注意：演示数据会写进 config 里的 update_days 保存目录和 data/qqark.db，
      正式用之前建议删掉 data/qqark.db（或用 --config 指定另一个配置文件）。
"""

from __future__ import annotations

import logging
import random
import struct
import time
import zlib
from pathlib import Path

log = logging.getLogger("qqark.mock")

GROUPS = [
    ("111222333", "高等数学答疑群", 486),
    ("444555666", "计算机视觉课程组", 213),
    ("777888999", "2024 毕设资料共享", 97),
]

SAMPLES = [
    ("第03讲 卷积神经网络.pdf", "文档", "群文件", 2_411_522),
    ("实验指导书 v2.docx", "文档", "群文件", 88_402),
    ("期末复习提纲.pptx", "幻灯片", "群文件", 5_120_004),
    ("数据集说明.md", "代码", "群文件", 4_211),
    ("课程录像 第5次课.mp4", "视频", "聊天视频", 48_233_100),
    ("答辩现场实拍.mp4", "视频", "聊天视频", 121_004_882),
    ("源码合集.zip", "压缩包", "群文件", 33_448_112),
    ("成绩登记表.xlsx", "表格", "群文件", 61_020),
    ("示意图.png", "图片", "聊天图片", 402_331),
    ("背景音乐 片段.mp3", "音频", "聊天文件", 3_004_112),
]


def install_mock(app) -> None:
    """把 app 的连接状态伪造成已连接，并灌入示例数据。"""
    app.ob.force_connected = True
    app.ob.self_id = "10001"
    app.ob.force_self_id = "10001"
    app.ob.nickname = "演示账号"
    app.ob.set_nickname("10001", "演示账号")
    # 没有 NapCat，任何 API 调用都走假数据（self_id 由多账号调用链透传进来）
    app.ob.call = lambda action, params=None, timeout=None, self_id=None: _fake_api(
        action or "", params or {})

    _seed(app)
    log.info("演示模式已启用（数据为示例，不会真正连接 QQ）")


def _fake_api(action: str, params: dict):
    if action == "get_group_list":
        return [{"group_id": g, "group_name": n, "member_count": m} for g, n, m in GROUPS]
    if action == "get_login_info":
        return {"user_id": 10001, "nickname": "演示账号"}
    if action == "get_group_info":
        gid = str(params.get("group_id") or "")
        for g, n, m in GROUPS:
            if g == gid:
                return {"group_id": g, "group_name": n, "member_count": m}
        return {}
    if action in ("get_group_root_files", "get_group_files_by_folder"):
        return _fake_files(str(params.get("group_id") or ""))
    if action == "get_group_file_url":
        return {"url": ""}
    if action == "get_file":
        return {"url": "", "file_size": 0}
    return {}


def _fake_files(group_id: str) -> dict:
    now = time.time()
    files = []
    tail = int(group_id[-1]) if group_id and group_id[-1].isdigit() else 0
    for i, (name, _cat, _src, size) in enumerate(SAMPLES):
        if i % 3 != tail % 3:
            continue
        files.append({
            "file_id": f"/mock/{group_id}/{i}",
            "file_name": name,
            "file_size": size,
            "busid": 102,
            "upload_time": int(now - 86400 * (i + 1)),
            "uploader": "演示用户",
            "uploader_id": "10001",
        })
    return {"files": files, "folders": []}


def _seed(app) -> None:
    root: Path = app.cfg.save_root
    db = app.db

    for gid, gname, members in GROUPS:
        db.upsert_group(gid, gname, members)

    now = time.time()
    for i, (name, cat, source, size) in enumerate(SAMPLES):
        gid, gname, _ = GROUPS[i % len(GROUPS)]
        folder = root / f"{gname}_{gid}" / time.strftime(
            "%Y-%m", time.localtime(now - 86400 * i)) / cat
        target = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _write_placeholder(target, cat, name)
            real_size = target.stat().st_size
        except OSError as exc:
            log.warning("演示文件写入失败 %s: %s", target, exc)
            continue
        db.add_file(
            group_id=gid, group_name=gname, file_id=f"/mock/{gid}/{i}", busid=102,
            file_name=name, save_path=str(target), size=max(real_size, 1),
            category=cat, source=source, uploader="演示用户", uploader_id="10001",
            msg_id=f"mock{i}", status="done", created_at=now - 86400 * i,
        )
    log.info("演示数据已写入 %s", root)


def _write_placeholder(path: Path, category: str, name: str) -> None:
    """生成能真正被浏览器打开的最小文件（图/音频/文本）。"""
    if path.exists() and path.stat().st_size > 0:
        return
    if category == "图片":
        path.write_bytes(_minimal_png(320, 180))
    elif category == "音频":
        path.write_bytes(_minimal_wav())
    elif category == "视频":
        # 不生成真视频（体积大且需要编码器），留 1 字节占位；预览会给出提示
        path.write_bytes(b"\x00")
    else:
        path.write_text(f"演示文件：{name}\n\n这是演示模式生成的占位内容。\n", encoding="utf-8")


def _minimal_png(w: int, h: int) -> bytes:
    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            r = 30 + int(60 * x / w)
            g = 60 + int(120 * y / h)
            b = 140 + int(80 * (1 - x / w))
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _minimal_wav(seconds: float = 2.0, rate: int = 8000) -> bytes:
    import math

    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(3000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<h", v)
    data = bytes(frames)
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
           + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", len(data)))
    return hdr + data
