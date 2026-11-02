"""下载器"没连接就不许领任务"回归测试。

这是 20 个文件被误标 failed 的直接伤害路径：worker 在没有任何账号
连上的情况下照样从库里领任务，换取链接时抛 `账号 xxx 未连接`，
重试 3 次后任务变成 failed —— 文件其实是好的。

本测试直接构造一个永远"未连接"的 OneBotClient，塞一条 pending 任务，
跑下载器 6 秒，断言任务原地不动。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 测试在 tests/ 下，仓库根在上一层
sys.path.insert(0, str(ROOT))

from qqark.config import Config  # noqa: E402
from qqark.db import Database  # noqa: E402
from qqark.downloader import Downloader  # noqa: E402


class OfflineOneBot:
    """永远报告未连接，任何换链接调用都算严重错误。"""

    def __init__(self):
        self.calls = 0

    def connected(self, self_id=None) -> bool:
        return False

    def get_group_file_url(self, *a, **kw):
        self.calls += 1
        raise AssertionError("未连接时不该去换下载链接")

    def get_file(self, *a, **kw):
        self.calls += 1
        raise AssertionError("未连接时不该去调 get_file")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qqark_dl_"))
    try:
        cfg = Config(tmp / "config.json")
        cfg.override("data_dir", str(tmp / "data"))
        cfg.override("save_root", str(tmp / "save"))
        (tmp / "data").mkdir(parents=True, exist_ok=True)
        (tmp / "save").mkdir(parents=True, exist_ok=True)

        db = Database(cfg.data_dir / "qqark.db")
        db.add_task(
            group_id="1",
            file_name="不该被碰的文件.pdf",
            size=1024,
            kind="group",
            file_id="fid",
            busid=102,
            account="100000000",
            dedupe_key="testkey0001",
        )
        before = db.count_tasks("pending")
        ob = OfflineOneBot()
        dl = Downloader(cfg, db, ob)
        dl.start()
        time.sleep(6)
        dl.stop()

        pending = db.count_tasks("pending")
        failed = db.count_tasks("failed")
        running = db.count_tasks("running")
        print(f"pending before={before} after={pending} failed={failed} "
              f"running={running} onebot_calls={ob.calls}")

        ok = pending == before and failed == 0 and running == 0 and ob.calls == 0
        if not ok:
            print("FAIL: 未连接时仍然碰了任务队列")
        else:
            print("PASS: 未连接时任务原地不动，没有去换链接")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
