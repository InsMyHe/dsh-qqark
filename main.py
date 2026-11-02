"""QQ 群文件 / 视频归档器 —— 启动器。

用法：
    python main.py                启动（自动打开浏览器）
    python main.py --no-browser   启动但不打开浏览器
    python main.py --port 6200    指定 WS 端口
    python main.py --check        只检查 NapCat 配置是否正确，不启动
    python main.py --mock         用假数据演示界面（不需要 NapCat）

正常使用只需要：先开 NapCat，再双击「启动.bat」（或 python main.py）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

LOG_FILE = ROOT / "data" / "qqark.log"


def _fix_console_encoding() -> None:
    """接管控制台编码，避免中文/制表符在 GBK 环境下把程序打崩。

    Windows 中文版默认 stdout 编码是 cp936(GBK)。`✓` `╔` 这类字符在 GBK 里
    没有对应字节，print 时会抛 UnicodeEncodeError 直接把程序干掉（实测过）。
    这里做两件事：
      1. 把 Python 输出流强制成 UTF-8，并且 errors="replace" 兜底永不抛异常
      2. 把 Windows 控制台代码页也切成 65001，让实际渲染也对得上
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="qqark",
        description="QQ 群文件 / 视频归档器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config", default="", help="配置文件路径（默认 ./config.json）")
    p.add_argument("--port", type=int, default=0, help="反向 WS 监听端口（默认 6200）")
    p.add_argument("--web-port", type=int, default=0, help="媒体库端口（默认 7801）")
    p.add_argument("--save-root", default="", help="文件保存根目录")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--log", default="INFO", help="控制台日志级别 DEBUG/INFO/WARNING")
    p.add_argument("--check", action="store_true", help="检查 NapCat 配置后退出")
    p.add_argument("--fix", action="store_true", help="配合 --check：自动追加反向 WS 配置（会先备份）")
    p.add_argument("--mock", action="store_true", help="用模拟数据跑界面（无需 NapCat）")
    return p.parse_args(argv)


def _print_abort(reason: str) -> None:
    print()
    print("  ┌──────────────────────────────────────────────────────┐")
    print("  │  启动被中止                                          │")
    print("  └──────────────────────────────────────────────────────┘")
    print(f"   {reason}")
    print()
    print("   常见原因：")
    print("     1. 已经有一个归档器窗口在运行（先关掉旧的再启动）")
    print("     2. 任务管理器里还有残留的 python 进程占用 6200 端口")
    print("   想确认是谁占着端口，可以运行：")
    print("     netstat -ano | findstr :6200")
    print()


def _pause_if_console() -> None:
    """双击 bat 启动时窗口会立刻关掉，读者根本看不到报错。

    只在 stdin 是控制台（不是被管道重定向）时暂停，避免卡住自动化调用。
    """
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("   按回车键关闭窗口…")
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        pass


def main(argv=None) -> int:
    _fix_console_encoding()
    args = parse_args(argv)

    if args.check:
        from qqark.napcat import main_check
        return main_check(auto_fix=args.fix)

    from qqark.app import QQArk, setup_logging

    setup_logging(args.log, LOG_FILE)

    cfg_path = Path(args.config) if args.config else ROOT / "config.json"
    app = QQArk(cfg_path)

    # 命令行参数覆盖配置（仅本次运行，不写回文件）
    # 用 override 而不是 set：set 会立刻落盘，导致下次双击启动还沿用这次的临时路径
    if args.port:
        app.cfg.override("ws_port", args.port)
    if args.web_port:
        app.cfg.override("web_port", args.web_port)
    if args.save_root:
        app.cfg.override("save_root", args.save_root)

    if args.mock:
        from qqark.mock import install_mock
        install_mock(app)

    info = app.info()
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║           QQ 群文件 / 视频归档器  v1.0               ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print(f"   媒体库     {info['web_url'] or '(启动中…)'}")
    print(f"   等待连接   {info['ws']}")
    print(f"   保存到     {info['save_root']}")
    print(f"   配置文件   {info['config']}")
    print(f"   运行日志   {LOG_FILE}")
    print()
    print("   请确保 NapCat 已启动并登录 QQ。若 NapCat 未配置连过来，")
    print("   运行  python main.py --check  查看具体改法。")
    print("   按 Ctrl+C 退出。")
    print()

    try:
        app.start(open_browser=None if not args.no_browser else False)
    except SystemExit as exc:
        # 端口被占等"启动即失败"的情况，app 里已经把原因打进日志和
        # launch_abort_reason，这里只负责友好收尾并给出非 0 退出码。
        _print_abort(app.launch_abort_reason or str(exc))
        _pause_if_console()
        return int(exc.code or 2) if isinstance(exc.code, int) else 2
    except Exception as exc:
        from qqark.lockfile import LockBusy
        if isinstance(exc, LockBusy):
            _print_abort(str(exc))
            _pause_if_console()
            return 3
        logging.getLogger("qqark").exception("启动失败")
        _pause_if_console()
        return 1

    try:
        while not app.should_stop():
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  正在退出…")
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
