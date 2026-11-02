"""单实例锁：保证同一份数据目录只被一个归档器进程使用。

为什么必须有（这不是洁癖，是丢过文件的教训）：
    两个实例共用同一个 SQLite 库时会发生这些事——
      1. 后启动的实例 reset_stuck_tasks() 把前一个正在下载的 running 任务
         改回 pending，前一个下完再写回，两边互相踩
      2. 后启动的实例绑不上 6200（被前一个占着），旧代码会"顺延到 6201"
         并打印"启动成功"，但它永远收不到 NapCat 的反向连接
      3. 于是这个没有连接的实例拿着任务去换下载链接，必然抛
         `账号 xxx 未连接`，重试 3 次后把任务标记为 failed
    实测后果：20 个真实存在的群文件被误标为失败，一个都没下下来。

锁的实现：操作系统级的文件字节范围锁（Windows 的 msvcrt.locking /
POSIX 的 flock）。进程退出时由内核自动释放，不会留下需要手动清理的
"僵尸锁文件"——这也是不用"写个 pid 文件再判断进程是否活着"的原因。

两个文件，各管一件事：
    <data_dir>/qqark.lock   纯锁，内容无关紧要，别往里写有用信息
    <data_dir>/qqark.pid    占用者 pid，明文，随时可读，只用于报错提示

为什么 pid 不写在锁文件里：Windows 的 LockFile 一旦锁住某段字节，
**其他句柄连整个文件都读不了**（实测 read(16) 直接 PermissionError，
连 seek 到别的偏移也一样）。所以锁和可读信息必须分文件放。
"""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

# 固定锁 16 字节：Windows 上锁 0 长度文件的行为跨版本不一致，垫一个字节最稳。
_LOCK_SIZE = 16


class LockBusy(Exception):
    """已有实例持有同一把锁。"""

    def __init__(self, path: Path, holder: str = ""):
        self.path = Path(path)
        self.holder = holder
        detail = f"（占用者 pid={holder}）" if holder else ""
        super().__init__(f"已有归档器实例在运行{detail}")


class SingleInstance:
    """进入 with 或显式 acquire()/release() 都可。"""

    def __init__(self, path: Path, pid_path: Path | None = None):
        self.path = Path(path)
        self.pid_path = Path(pid_path) if pid_path else self.path.with_suffix(".pid")
        self._fh = None

    # ---------- 对外 ----------

    def acquire(self) -> "SingleInstance":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 不能用 "a+"：追加模式下无论 seek 到哪里，写入都会跑到文件末尾。
        if not self.path.exists():
            with open(self.path, "wb"):
                pass
        fh = open(self.path, "r+b")
        try:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size < _LOCK_SIZE:
                fh.write(b"\0" * (_LOCK_SIZE - size))
                fh.flush()
            fh.seek(0)
            try:
                self._lock(fh)
            except OSError:
                raise LockBusy(self.path, self.holder_pid()) from None
        except BaseException:
            # 任何失败都要关掉句柄，否则会把锁"带走"
            try:
                fh.close()
            except OSError:
                pass
            raise
        self._fh = fh
        self._write_pid()
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            self._unlock(fh)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "SingleInstance":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

    # ---------- pid 记录（纯提示用，不参与判定） ----------

    def _write_pid(self) -> None:
        try:
            self.pid_path.write_text(str(os.getpid()), encoding="ascii")
        except OSError:
            pass

    def holder_pid(self) -> str:
        """读占用者 pid。锁是死的、pid 只是提示，读不到就当空。"""
        try:
            return self.pid_path.read_text(encoding="ascii").strip()
        except OSError:
            return ""

    # ---------- 平台细节 ----------

    @staticmethod
    def _lock(fh) -> None:
        if os.name == "nt":
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, _LOCK_SIZE)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fh) -> None:
        if os.name == "nt":
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, _LOCK_SIZE)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class InstanceLock:
    """按数据目录取锁的便捷封装。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "qqark.lock"
        self.pid_path = self.data_dir / "qqark.pid"
        self._impl = SingleInstance(self.path, self.pid_path)

    def acquire(self) -> "InstanceLock":
        self._impl.acquire()
        return self

    def release(self) -> None:
        self._impl.release()

    @property
    def locked(self) -> bool:
        return self._impl._fh is not None

    def holder_pid(self) -> str:
        return self._impl.holder_pid()
