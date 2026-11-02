#!/usr/bin/env python3
"""扫描被 git 跟踪的文件，找出泄漏本机身份的内容。

四类别扫：

1. **写死的 Windows 用户目录**：`C:\\Users\\<某人>\\...`。它既暴露作者是谁，
   也让别人 clone 下来直接跑不通。代码注释里那种 `C:\\Users\\...\\课程`
   的示意写法不算（用户名段被省略成点），所以正则要排掉纯点号。
2. **YOUR_QQ / 群号一类的硬编码个人数据**：靠本文件之外的词表（环境变量
   `QQARK_FORBIDDEN`，逗号分隔）传入，仓库里不存任何要防的字面量——
   否则这个检查脚本自己就成了泄漏点。
3. **作者的主目录名**：从 `$HOME` 推出来，命中即报。
4. **本机真实库里的群名与文件名**：如果 `data/qqark.db` 存在，就把里面
   真实出现过的群名、群号、文件名当词表，扫一遍仓库文本。这一类的坑在于
   **测试和文档里的示例很容易顺手抄真实数据**——比如写回归测试时把当时
   调试用的真文件名粘进 `_run(...)`。库不存在时（CI、别人的机器）自动跳过，
   所以脚本自身仍然不含任何真实字面量。

用法：python scripts/check_no_personal_paths.py
有命中时打印 `文件:行号:内容` 并以 1 退出。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# 匹配 Windows 用户目录，但用户名段不能只有点号（那是代码注释里的占位写法）
WIN_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!\.{2,}(?:[\\/]|$))[A-Za-z0-9][A-Za-z0-9._-]*")

# 本脚本自己要写清楚在找什么，天生含示例字符串，扫描时跳过自身
SELF = Path(__file__).resolve()

# 只扫文本类文件；二进制里偶尔会撞出巧合字节
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".yml", ".yaml", ".md",
    ".txt", ".bat", ".cmd", ".ps1", ".sh", ".toml", ".cfg", ".ini", ".html", ".css",
}


SKIP_DIRS = {".git", "__pycache__", "node_modules", "_selftest", "_check", "data"}


def tracked_files() -> list[Path]:
    """被跟踪的文件；不在 git 仓库里（比如导出后的目录）就退回遍历文件系统。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.decode("utf-8", "replace")
        files = [Path(p) for p in out.split("\0") if p]
        if files:
            return files
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass

    root = Path(__file__).resolve().parent.parent
    return [
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS)
    ]


def _local_db() -> Path | None:
    """找出本机真实库。标准库只用来当「要防的词表」来源，不参与比对输出。

    依次试：环境变量显式指定 → 仓库内 data/ → 仓库上一级 data/
    （开发时的常见布局：归档器数据目录与代码目录并列）。
    都找不到就返回 None，第 4 类检查自动跳过。
    """
    env = os.environ.get("QQARK_LOCAL_DB", "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None

    root = Path(__file__).resolve().parent.parent
    for cand in (root / "data" / "qqark.db", root.parent / "data" / "qqark.db"):
        if cand.is_file():
            return cand
    return None


def _local_corpus() -> list[str]:
    """从本机真实库里取群名/群号/文件名，当作词表。

    这些值只存在于运行时内存里，绝不写进仓库。库不存在（CI 或别人的机器）
    就直接返回空列表，此时这一段检查自动失效，不影响其余三类。
    """
    db = _local_db()
    if db is None:
        return []

    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except Exception:
        return []

    words: set[str] = set()
    try:
        for (name,) in con.execute("SELECT group_name FROM groups"):
            if name and len(name.strip()) >= 4:
                words.add(name.strip())
        for (gid,) in con.execute("SELECT group_id FROM groups"):
            if gid and len(str(gid)) >= 6:
                words.add(str(gid))
        for (fname,) in con.execute("SELECT file_name FROM files"):
            if fname and len(fname.strip()) >= 5:
                words.add(fname.strip())
    except sqlite3.Error:
        return []
    finally:
        con.close()

    # 太通用的词（如 "index.html"）会满屏误报，剔掉纯 ASCII 的常见文件名
    generic = {"index.html", "main.py", "config.json", "README.md"}
    return sorted(w for w in words if w not in generic)


def build_patterns() -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = [("windows 用户目录", WIN_HOME)]

    extra = [w for w in os.environ.get("QQARK_FORBIDDEN", "").split(",") if w.strip()]
    if extra:
        patterns.append(("硬编码个人数据", re.compile("|".join(re.escape(w.strip()) for w in extra))))

    home = Path.home().name
    if home and home.lower() not in {"root", "user", "runner", "admin"}:
        patterns.append(("作者主目录名", re.compile(re.escape(home))))

    corpus = _local_corpus()
    if corpus:
        # 长词优先，避免短词先匹配把长词的命中切碎
        corpus.sort(key=len, reverse=True)
        patterns.append(("本机真实群名/文件名", re.compile("|".join(re.escape(w) for w in corpus))))

    return patterns


def main() -> int:
    patterns = build_patterns()
    hits: list[str] = []

    for path in tracked_files():
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
            continue
        if path.resolve() == SELF:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pat in patterns:
                if pat.search(line):
                    hits.append(f"{path}:{lineno}: [{label}] {line.strip()[:160]}")
                    break

    if hits:
        print(f"[!!] 发现 {len(hits)} 处可能泄漏本机身份的内容：")
        for h in hits:
            print(f"     {h}")
        print("     修法：改成 ~、相对路径或占位值；不要把真实路径/号码提交进仓库。")
        return 1

    print(f"[OK] 扫描 {len(tracked_files())} 个被跟踪文件，未发现本机身份泄漏")
    return 0


if __name__ == "__main__":
    sys.exit(main())
