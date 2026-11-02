"""QQ 群文件 / 视频归档器。"""

__version__ = "1.0.0"
__all__ = ["QQArk"]


def __getattr__(name):
    if name == "QQArk":
        from .app import QQArk as _QQArk
        return _QQArk
    raise AttributeError(name)
