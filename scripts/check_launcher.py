#!/usr/bin/env python3
"""校验「启动.bat」的字节约束。

cmd.exe 对批处理的要求很具体，两条都不能破：

1. **只能有 ASCII**：`chcp 65001` 生效之前，cmd 会先用当前代码页解释那一行。
   UTF-8 中文被当 GBK 解成乱码，乱码还会吞掉换行，把下一行命令粘进来。
2. **换行必须 CRLF**：cmd 逐行解析，遇到 LF 会把 `if (...)` 多行块读成一整行，
   后面的命令全部错乱，表现是满屏「系统找不到指定的路径」。

用法：python scripts/check_launcher.py [文件路径]
不改文件，只报错并给出退出码。
"""

from __future__ import annotations

import sys
from pathlib import Path


def check(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_bytes()

    non_ascii = [(i, b) for i, b in enumerate(raw) if b > 0x7F]
    if non_ascii:
        offsets = ", ".join(str(i) for i, _ in non_ascii[:8])
        problems.append(f"含 {len(non_ascii)} 个非 ASCII 字节，首个在偏移 {offsets}")

    total_lf = raw.count(b"\n")
    total_crlf = raw.count(b"\r\n")
    if total_lf == 0:
        problems.append("没有任何换行，文件可能是空的或只有一行")
    elif total_lf != total_crlf:
        problems.append(f"换行不全是 CRLF（{total_lf} 个 LF 中只有 {total_crlf} 个 CRLF）")

    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("开头有 UTF-8 BOM，cmd 会把 BOM 当命令的一部分")

    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "启动.bat")
    if not path.exists():
        print(f"[!!] 找不到 {path}")
        return 1

    problems = check(path)
    if problems:
        print(f"[!!] {path} 不满足 cmd.exe 的要求：")
        for p in problems:
            print(f"     · {p}")
        print("     修法：把文件另存为纯 ASCII + CRLF。仓库 .gitattributes 已声明 *.bat text eol=crlf。")
        return 1

    print(f"[OK] {path}：纯 ASCII + 全 CRLF")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
