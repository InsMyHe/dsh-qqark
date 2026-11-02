"""file_id 过期后自动重新列举群文件、刷新并重试 的回归测试。

背景（这是 20 个文件被误标 failed 的第二层原因，第一层是两个实例）：
NapCat 每次列举群文件都会重新生成 file_id，库里存下来的 file_id 过一段时间
就换不到下载链接了，返回 `retcode=1200 real fileUUID not found!`。
老代码收到空 url 直接判失败，文件明明在群里却永远下不来。

本测试用假 OneBot 模拟这件事：拿旧 file_id 换链接一律失败，但
find_group_file 能按文件名找到新 file_id，新 file_id 换链接成功。
断言：任务变成 done、磁盘上真有文件、库里的 file_id 被写成新的。
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

OLD_ID = "olddeadbeef"
NEW_ID = "newcafe1234"
PAYLOAD = b"hello qqark " * 64


class ExpiringOneBot:
    """旧 file_id 一律 1200，新 file_id 才给 url。"""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.url_calls = 0
        self.find_calls = 0
        self._scan_errors: list[str] = []

    def connected(self, self_id=None) -> bool:
        return True

    def get_group_file_url(self, group_id, file_id, busid=0, self_id=None) -> str:
        self.url_calls += 1
        if file_id == NEW_ID:
            return self.base_url
        raise OneBotError("get_group_file_url 失败 retcode=1200 msg=real fileUUID not found!")

    def get_file(self, file_id, self_id=None) -> dict:
        raise OneBotError("get_file 失败 retcode=1200 msg=file not found")

    def find_group_file(self, group_id, file_name, size=0, self_id=None, max_depth=3):
        self.find_calls += 1
        return {"file_id": NEW_ID, "file_size": size, "busid": 102}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qqark_refresh_"))
    srv = None
    try:
        cfg = Config(tmp / "config.json")
        cfg.override("data_dir", str(tmp / "data"))
        cfg.override("save_root", str(tmp / "save"))
        for d in (tmp / "data", tmp / "save", tmp / "data" / "tmp"):
            d.mkdir(parents=True, exist_ok=True)

        # 起一个只吐固定字节的 HTTP 服务当作"QQ 文件服务器"
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Length", str(len(PAYLOAD)))
                self.end_headers()
                self.wfile.write(PAYLOAD)

            def log_message(self, *a):
                pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}/f"

        db = Database(cfg.data_dir / "qqark.db")
        db.add_task(
            kind="group_file",
            group_id="100000001",
            group_name="测试群",
            file_id=OLD_ID,
            busid=102,
            file_name="过期链接.pptx",
            size=len(PAYLOAD),
            source="群文件",
            account="100000000",
            dedupe_key="refreshkey01",
        )

        ob = ExpiringOneBot(base_url)
        dl = Downloader(cfg, db, ob)
        task = db.next_tasks(1)[0]
        db.claim_task(task["id"])
        dl._process(task)

        state = db.conn().execute(
            "SELECT state, file_id, last_error FROM tasks WHERE id=?", (task["id"],)
        ).fetchone()
        row = db.conn().execute(
            "SELECT save_path, size FROM files WHERE file_name=?", ("过期链接.pptx",)
        ).fetchone()
        on_disk = bool(row) and Path(row["save_path"]).exists()
        disk_size = Path(row["save_path"]).stat().st_size if on_disk else -1

        print(f"task_state={state['state']} db_file_id={state['file_id']} "
              f"find_calls={ob.find_calls} url_calls={ob.url_calls}")
        print(f"saved={row['save_path'] if row else '-'} on_disk={on_disk} size={disk_size}")

        ok = (
            state["state"] == "done"
            and state["file_id"] == NEW_ID
            and on_disk
            and disk_size == len(PAYLOAD)
            and ob.find_calls >= 1
        )
        if not ok:
            print(f"FAIL last_error={state['last_error']}")
        else:
            print("PASS: 旧 file_id 失效 → 重新列举 → 用新 file_id 下载成功")
        httpd.shutdown()
        return 0 if ok else 1
    finally:
        if srv:
            srv.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
