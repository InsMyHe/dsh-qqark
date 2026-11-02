"""「群里已经没有这个文件了」→ 终态 gone 的回归测试。

背景（这是 74 条任务永远卡在 failed 的真因）：
QQ 群文件有服务端保留期。8-9 月的老文件实体被清理后，群文件列表里**仍然
留着文件名**，但换下载链接会稳定报
`sendGroupFileDownloadReq error: 文件不存在 (code=-103)`。
实测失败率随文件年龄单调上升：8 月 72%、9 月 36%、10 月 0%。

老代码把这种情况记成 failed 并重试 3 次，后果有两层：
  1. 界面上「失败 74」看着像"还有救"，用户会一直点重试；
  2. 每次重试都要完整遍历一遍群目录树 —— 74×3=222 次，把 NapCat 拖到
     列目录超时，超时又被判成"没读到目录"，于是重试更多次，正反馈。

本测试模拟两种情况，断言它们被严格区分开：
  - 目录树读得干干净净，但树里没有这个文件名 → **gone**（终态，不再重试）
  - 列目录本身出错（读了一半）→ **failed**（必须保留重试机会）
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 测试在 tests/ 下，仓库根在上一层
sys.path.insert(0, str(ROOT))

from qqark.config import Config  # noqa: E402
from qqark.db import Database  # noqa: E402
from qqark.downloader import Downloader  # noqa: E402
from qqark.onebot import OneBotError  # noqa: E402


class VanishedOneBot:
    """文件列表里已经没有它了，但目录树能完整读下来。"""

    def __init__(self):
        self.find_calls = 0
        self._scan_errors: list[str] = []

    def connected(self, self_id=None) -> bool:
        return True

    def get_group_file_url(self, group_id, file_id, busid=0, self_id=None) -> str:
        raise OneBotError(
            "get_group_file_url 失败 retcode=1200 msg=sendGroupFileDownloadReq error: 文件不存在 (code=-103)"
        )

    def get_file(self, file_id, self_id=None) -> dict:
        raise OneBotError("get_file 失败 retcode=1200 msg=file not found")

    def find_group_file(self, group_id, file_name, size=0, self_id=None, max_depth=3):
        self.find_calls += 1
        # 关键：没找到，而且没有读目录错误 → 这就是"真的没了"
        self._scan_errors = []
        return None


class HalfReadOneBot(VanishedOneBot):
    """列目录时读挂了一部分 —— 不能因此判定文件没了。"""

    def find_group_file(self, group_id, file_name, size=0, self_id=None, max_depth=3):
        self.find_calls += 1
        self._scan_errors = ["folder_9: 调用 get_group_files_by_folder 超时（60.0s）"]
        return None


def _run(ob, tmp: Path, name: str) -> dict:
    cfg = Config(tmp / "config.json")
    cfg.override("data_dir", str(tmp / "data"))
    cfg.override("save_root", str(tmp / "save"))
    for d in (tmp / "data", tmp / "save", tmp / "data" / "tmp"):
        d.mkdir(parents=True, exist_ok=True)

    db = Database(cfg.data_dir / "qqark.db")
    db.add_task(
        kind="group_file",
        group_id="100000001",
        group_name="测试群",
        file_id="5f5e8d53",
        busid=102,
        file_name=name,
        size=123456,
        source="群文件",
        account="100000000",
        dedupe_key="gonekey" + name[-6:],
    )
    dl = Downloader(cfg, db, ob)
    task = db.next_tasks(1)[0]
    db.claim_task(task["id"])
    dl._process(task)
    row = db.conn().execute(
        "SELECT state, last_error FROM tasks WHERE id=?", (task["id"],)
    ).fetchone()
    return {"state": row["state"], "last_error": row["last_error"],
            "pending": db.count_tasks("pending"), "find_calls": ob.find_calls}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qqark_gone_"))
    failures: list[str] = []
    try:
        vanished = _run(VanishedOneBot(), tmp / "a", "选修课选课攻略.xlsx")
        print(f"vanished: state={vanished['state']} "
              f"pending={vanished['pending']} find_calls={vanished['find_calls']}")
        print(f"  last_error={vanished['last_error']}")
        if vanished["state"] != "gone":
            failures.append(f"文件真没了却记成 {vanished['state']}，应为 gone")
        if vanished["pending"] != 0:
            failures.append(f"gone 的任务被放回了队列（pending={vanished['pending']}）")

        half = _run(HalfReadOneBot(), tmp / "b", "内务评选参考办法-改 (1).pdf")
        print(f"half-read: state={half['state']} pending={half['pending']}")
        print(f"  last_error={half['last_error']}")
        if half["state"] != "failed":
            failures.append(f"目录没读全就判死，state={half['state']}，应为 failed")
        if "部分目录读取失败" not in (half["last_error"] or ""):
            failures.append(f"读不全时的文案没说清原因：{half['last_error']}")

        if failures:
            for f in failures:
                print(f"FAIL {f}")
            return 1
        print("PASS: 文件真没了→gone（终态不再重试）；目录没读全→failed（保留重试）")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
