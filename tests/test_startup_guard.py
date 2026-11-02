"""启动失败路径回归测试：WS 端口被占时必须整体失败、且不能把单实例锁带走。

为什么单独立这个测试：早期实现里 `_start_ws` 绑不上端口只记一条 error
然后继续启动，于是"没有 NapCat 连接、却照样领任务下载"的僵尸实例存在了
两小时，把 20 个真实文件误标成 failed。修完之后要能证明：
  1. 绑定失败 → 抛 SystemExit(2)，不是静默继续
  2. 抛之前 worker 线程还没起
  3. 锁被释放掉，用户关掉占端口的程序后能立刻重新启动
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 测试在 tests/ 下，仓库根在上一层
sys.path.insert(0, str(ROOT))

from qqark.app import QQArk, setup_logging  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    setup_logging("ERROR")
    tmp = Path(tempfile.mkdtemp(prefix="qqark_fail_"))
    ws_port, web_port = free_port(), free_port()
    cfg_path = tmp / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "data_dir": str(tmp / "data"),
                "save_root": str(tmp / "save"),
                "ws_port": ws_port,
                "web_port": web_port,
                "scan_enabled": False,
                "auto_open_browser": False,
                "watch_groups": ["1"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", ws_port))
    blocker.listen(1)

    ok = True
    try:
        for round_no in (1, 2):
            app = QQArk(cfg_path)
            try:
                app.start(open_browser=False)
                print(f"[{round_no}] 竟然启动成功了 —— 不符合预期")
                ok = False
                app.stop()
            except SystemExit as exc:
                code = exc.code
                print(f"[{round_no}] SystemExit code={code} lock_held={app._lock is not None} "
                      f"workers={len(app.dl._workers)} reason={app.launch_abort_reason[:28]}…")
                if code != 2:
                    ok = False
                if app._lock is not None:
                    print("        ✘ 锁没释放，下次启动会被自己挡住")
                    ok = False
                if app.dl._workers:
                    print("        ✘ worker 线程被提前点着了")
                    ok = False
            except BaseException as exc:  # noqa: BLE001
                print(f"[{round_no}] 意外异常 {type(exc).__name__}: {exc}")
                ok = False
    finally:
        blocker.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
