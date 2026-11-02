"""回归测试：列举群文件时必须显式传 file_count，不能用 NapCat 的默认值 50。

为什么值得单独一个测试
----------------------
NapCat 的 get_group_root_files / get_group_files_by_folder 接受一个 file_count
参数，**默认只有 50**（napcat.mjs 里 `file_count: p.Union(..., { default: 50 })`）。
它内部确实有翻页循环，但循环的退出条件是

    if (!o?.item?.length || (r.push(...o.item), o.isEnd) || r.length === n.fileCount) break;

也就是说：只要攒够 n.fileCount 个就停。不传这个参数的话，每个目录永远只回来
前 50 个文件，第 51 个以后看不见。

这个 bug 的危害不是"少抓几个文件"那么轻：
  1) 补扫漏文件，用户会发现某些群文件永远没被下载；
  2) 更糟的是 find_group_file 在整棵树里找不到它 → 被判定成「群里已无此文件」
     （gone 终态）→ 那个文件再也不会被尝试，界面上还显示得很有道理。

所以这里钉死两件事：
  - 默认请求里 file_count 存在且远大于 50；
  - 调用方显式传入的值优先（允许为特殊场景调小）。
"""

import sys
import types
from pathlib import Path

# 测试在 tests/ 下，仓库根在上一层；qqark 包在那儿。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


def make_client():
    from qqark.onebot import OneBotClient

    ob = OneBotClient.__new__(OneBotClient)   # 不走 __init__，只测参数拼装
    calls = []

    def fake_call(action, data=None, **kwargs):
        calls.append((action, dict(data or {})))
        if action in ("get_group_root_files", "get_group_files_by_folder"):
            return {"files": [], "folders": []}
        return {}

    ob.call = fake_call
    return ob, calls


def main():
    ob, calls = make_client()

    print("1) 默认值必须远大于 50")
    check("LIST_FILE_COUNT 存在", hasattr(ob, "LIST_FILE_COUNT"))
    check("LIST_FILE_COUNT > 50（NapCat 默认值）",
          int(getattr(ob, "LIST_FILE_COUNT", 0)) > 50,
          f"= {getattr(ob, 'LIST_FILE_COUNT', None)}")

    print("2) 根目录列举要带上 file_count")
    ob.get_group_root_files("123456")
    action, data = calls[-1]
    check("action 是 get_group_root_files", action == "get_group_root_files", action)
    check("带了 file_count", "file_count" in data, data)
    check("file_count > 50", int(data.get("file_count") or 0) > 50, data)

    print("3) 子目录列举也要带（同一个默认值陷阱）")
    ob.get_group_files_by_folder("123456", "folder_abc")
    action, data = calls[-1]
    check("action 是 get_group_files_by_folder", action == "get_group_files_by_folder", action)
    check("带了 folder_id", data.get("folder_id") == "folder_abc", data)
    check("带了 file_count 且 > 50", int(data.get("file_count") or 0) > 50, data)

    print("4) 调用方显式传值优先")
    ob.get_group_root_files("123456", file_count=7)
    _, data = calls[-1]
    check("显式 file_count=7 生效", int(data.get("file_count") or 0) == 7, data)

    ob.get_group_files_by_folder("123456", "f1", file_count=3)
    _, data = calls[-1]
    check("子目录显式 file_count=3 生效", int(data.get("file_count") or 0) == 3, data)

    print("5) 不能出现「传 0 就当成没传」导致的静默降级")
    ob.get_group_root_files("123456", file_count=0)
    _, data = calls[-1]
    check("file_count=0 时回落到默认值（不会真的请求 0 个）",
          int(data.get("file_count") or 0) > 50, data)

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} 项 —— {', '.join(FAILS)}")
        return 1
    print("PASS: 列群文件时始终显式传 file_count，不会只拿到前 50 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
