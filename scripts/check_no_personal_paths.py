#!/usr/bin/env python3
"""扫描被 git 跟踪的文件，找出泄漏本机身份的内容。

三类别扫：

1. **写死的 Windows 用户目录**：`C:\\Users\\<某人>\\...`。它既暴露作者是谁，
   也让别人 clone 下来直接跑不通。代码注释里那种 `C:\\Users\\...\\课程`
   的示意写法不算（用户名段被省略成点），所以正则要排掉纯点号。
2. **YOUR_QQ / 群号一类的硬编码个人数据**：靠本文件之外的词表（环境变量
   `QQARK_FORBIDDEN`，逗号分隔）传入，仓库里不存任何要防的字面量——
   否则这个检查脚本自己就成了泄漏点。
3. **作者的主目录名**：从 `$HOME` 推出来，命中即报。

用法：python scripts/check_no_personal_paths.py
有命中时打印 `文件:行号:内容` 并以 1 退出。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# 匹配 C:\Users\xxx 或 C:/Users/xxx，但用户名段不能只有点号（那是占位写法）
WIN_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!\.{2,}(?:[\\/]|$))[A-Za-z0-9][A-Za-z0-9._-]*")

# 只扫文本类文件；二进制里偶尔会撞出巧合字节
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".yml", ".yaml", ".md",
    ".txt", ".bat", ".cmd", ".ps1", ".sh", ".toml", ".cfg", ".ini", ".html", ".css",
}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    return [Path(p) for p in out.split("\0") if p]


def build_patterns() -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = [("windows 用户目录", WIN_HOME)]

    extra = [w for w in os.environ.get("QQARK_FORBIDDEN", "").split(",") if w.strip()]
    if extra:
        patterns.append(("硬编码个人数据", re.compile("|".join(re.escape(w.strip()) for w in extra))))

    home = Path.home().name
    if home and home.lower() not in {"root", "user", "runner", "admin"}:
        patterns.append(("作者主目录名", re.compile(re.escape(home))))

    return patterns


def main() -> int:
    patterns = build_patterns()
    hits: list[str] = []

    for path in tracked_files():
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
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
