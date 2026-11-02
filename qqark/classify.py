"""文件分类与路径生成。

分类维度：扩展名 → 类别目录；再按群 / 年月 / 类别 组织目录。
文件名做 Windows 非法字符清洗 + 长度截断 + 同名冲突加短哈希。
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

# 扩展名 → 中文类别目录名
EXT_MAP = {
    # 视频
    "mp4": "视频", "mkv": "视频", "avi": "视频", "mov": "视频", "wmv": "视频",
    "flv": "视频", "webm": "视频", "m4v": "视频", "ts": "视频", "rmvb": "视频",
    "rm": "视频", "3gp": "视频", "mpg": "视频", "mpeg": "视频", "vob": "视频",
    # 文档
    "doc": "文档", "docx": "文档", "pdf": "文档", "txt": "文档", "rtf": "文档",
    "wps": "文档", "odt": "文档", "md": "文档", "epub": "文档", "mobi": "文档",
    "xls": "表格", "xlsx": "表格", "csv": "表格", "et": "表格", "ods": "表格",
    "ppt": "幻灯片", "pptx": "幻灯片", "dps": "幻灯片", "odp": "幻灯片",
    # 压缩包
    "zip": "压缩包", "rar": "压缩包", "7z": "压缩包", "tar": "压缩包",
    "gz": "压缩包", "bz2": "压缩包", "xz": "压缩包", "iso": "压缩包",
    "cab": "压缩包", "zst": "压缩包",
    # 图片
    "jpg": "图片", "jpeg": "图片", "png": "图片", "gif": "图片", "bmp": "图片",
    "webp": "图片", "heic": "图片", "tif": "图片", "tiff": "图片", "svg": "图片",
    "psd": "图片", "ai": "图片",
    # 音频
    "mp3": "音频", "wav": "音频", "flac": "音频", "aac": "音频", "m4a": "音频",
    "ogg": "音频", "wma": "音频", "opus": "音频", "ape": "音频",
    # 程序/安装包
    "exe": "软件", "msi": "软件", "apk": "软件", "dmg": "软件", "pkg": "软件",
    "deb": "软件", "rpm": "软件", "appimage": "软件", "jar": "软件",
    # 代码
    "py": "代码", "js": "代码", "ts": "代码", "java": "代码", "c": "代码",
    "cpp": "代码", "h": "代码", "cs": "代码", "go": "代码", "rs": "代码",
    "html": "代码", "css": "代码", "json": "代码", "xml": "代码", "sql": "代码",
    "sh": "代码", "bat": "代码", "ipynb": "代码",
}

# 媒体类别（用于前端筛选和缩略图）
MEDIA_CATEGORIES = {"视频", "图片", "音频"}

# Windows 文件名非法字符
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def ext_of(name: str) -> str:
    """取小写扩展名（不含点）。"""
    name = name or ""
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower().strip()


def category_of(name: str, default: str = "其他") -> str:
    """按扩展名判断类别。"""
    return EXT_MAP.get(ext_of(name), default)


def is_media(name: str) -> bool:
    return category_of(name) in MEDIA_CATEGORIES


def safe_name(name: str, max_len: int = 120) -> str:
    """把任意文件名清洗成 Windows 合法名。

    - 替换非法字符为下划线
    - 去掉首尾空格与点
    - 规避 CON/PRN 等保留名
    - 总长度截断（保留扩展名）
    """
    name = (name or "").strip()
    if not name:
        name = "unnamed"
    name = _BAD_CHARS.sub("_", name)
    name = name.strip(" .")

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if ext and len(ext) > 16:
        stem, ext = name, ""

    stem = stem.strip(" .") or "unnamed"
    if stem.lower() in _RESERVED:
        stem = f"_{stem}"

    if ext:
        keep = max(8, max_len - len(ext) - 1)
        stem = stem[:keep]
        result = f"{stem}.{ext}"
    else:
        result = stem[:max_len]
    return result


def short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:n]


def dedupe_key(group_id: str, file_name: str, size: int) -> str:
    """稳定去重键（群号 + 文件名 + 字节数）。

    NapCat 的 file_id 每次列举都会重新生成（实测同一文件三次扫描拿到三个不同
    file_id），所以绝不能拿它判断"是否已经抓过"。

    这里刻意**不**把上传时间算进去：上传通知事件（group_upload）里没有上传
    时间，而补扫能拿到，若键里含时间，两条路径算出的键就不同，同一个文件仍会
    被抓两遍。同名同大小视为同一文件 —— 对课程资料这类场景，宁可少存一份也
    不要重复占满磁盘。
    """
    raw = f"{group_id}|{file_name}|{int(size or 0)}"
    return short_hash(raw, 16)


def month_of(ts: float | None = None) -> str:
    t = time.localtime(ts if ts else time.time())
    return f"{t.tm_year:04d}-{t.tm_mon:02d}"


def stamp_of(ts: float | None = None) -> str:
    t = time.localtime(ts if ts else time.time())
    return f"{t.tm_year:04d}{t.tm_mon:02d}{t.tm_mday:02d}_{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}"


def build_relpath(
    *,
    root: Path,
    layout: str,
    group_name: str,
    group_id: str,
    file_name: str,
    ts: float | None = None,
    source: str = "群文件",
    uploader: str = "",
    uniq: str = "",
) -> Path:
    """生成分类后的相对路径（不含 root）。

    layout:
      group_date_type  → 群名_ID/年-月/类别/文件名
      group_type       → 群名_ID/类别/文件名
      date_type        → 年-月/类别/文件名
      type_only        → 类别/文件名
    """
    cat = category_of(file_name)
    safe_group = safe_name(f"{group_name}_{group_id}" if group_name else f"群{group_id}", 80)
    month = month_of(ts)
    fname = safe_name(file_name, 120)

    parts: list[str] = []
    if layout == "group_date_type":
        parts = [safe_group, month, cat]
    elif layout == "group_type":
        parts = [safe_group, cat]
    elif layout == "date_type":
        parts = [month, cat]
    elif layout == "type_only":
        parts = [cat]
    else:  # 兜底
        parts = [safe_group, month, cat]

    target_dir = Path(root, *parts)
    path = target_dir / fname

    # 同名冲突：加来源/上传者/时间戳前缀区分，而不是覆盖
    if path.exists():
        prefix_bits = [b for b in (source, uploader) if b]
        prefix = "_".join(safe_name(b, 24) for b in prefix_bits)
        stem, dot, ext = fname.rpartition(".")
        if not dot:
            stem, ext = fname, ""
        candidate = f"{stem}_{stamp_of(ts)}{('_' + prefix) if prefix else ''}{('.' + ext) if ext else ''}"
        path = target_dir / safe_name(candidate, 140)
    if path.exists() and uniq:
        stem, dot, ext = path.name.rpartition(".")
        if not dot:
            stem, ext = path.name, ""
        path = target_dir / safe_name(f"{stem}_{uniq}{('.' + ext) if ext else ''}", 150)
    return path
