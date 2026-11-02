"""配置管理：JSON 文件持久化，带默认值与热重载。"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("qqark.config")

DEFAULT_CONFIG = {
    # ---- 网络 ----
    "ws_host": "127.0.0.1",
    # 与 NapCat onebot11_*.json 的 websocketClients.url 对应。
    # 不用 6199：那是 AstrBot 的 aiocqhttp 反向 WS 默认端口，占了会导致两边都收不到。
    "ws_port": 6200,
    "ws_path": "/ws",
    "ws_token": "",           # 反向 WS 校验 token，留空表示不校验
    "http_api": "",           # 可选：NapCat HTTP 服务地址，如 http://127.0.0.1:3000（反向 WS 不通时兜底）
    "http_token": "",
    "web_host": "127.0.0.1",
    "web_port": 7801,         # 本地媒体库端口

    # ---- 存储 ----
    # 默认落在当前用户主目录下的 QQArk/（跨平台，首次启动自动创建）。
    # 想换到别的盘（比如 C 盘会满），在界面「设置」里改，或启动时加 --save-root。
    "save_root": str(Path.home() / "QQArk"),
    "layout": "group_date_type",   # group_date_type | group_type | date_type | type_only
    "data_dir": "",                # 留空 = 程序目录下 data/

    # ---- 抓取 ----
    "watch_groups": [],            # 空表示监听全部群；填群号则只抓这些群
    "grab_group_files": True,      # 群文件（group_upload 事件）
    "grab_chat_video": True,       # 聊天里直接发的视频
    "grab_chat_file": True,        # 聊天里直接发的文件消息
    "grab_images": False,
    "grab_audio": False,

    # ---- 补扫 ----
    "scan_enabled": True,
    "scan_interval_min": 30,       # 分钟
    "scan_on_connect": True,
    "scan_depth": 3,               # 群文件夹递归深度

    # ---- 下载 ----
    "concurrency": 3,
    "retry": 3,
    "retry_delay": 5,              # 秒
    "timeout": 60,
    "chunk_size": 1048576,         # 1MB
    "min_size": 0,                 # 小于此字节数不下载（过滤空文件）
    "max_size_mb": 0,              # 0 = 不限制
    "dedupe": True,                # 按 大小+哈希 去重
    "skip_existing": True,         # 同路径同大小已存在则跳过

    # ---- 其它 ----
    "auto_open_browser": True,
    "keep_days": 0,                # 0 = 永久保留；>0 则清理 N 天前的索引记录（不删文件）
}

# 区分"键不存在"和"键存在但值是 None"
_MISSING = object()


class Config:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = dict(DEFAULT_CONFIG)
        # 被 override() 临时改过、但不该落盘的键 -> 它原本在磁盘上的值
        self._overridden: dict[str, object] = {}
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            log.exception("配置文件损坏，使用默认配置: %s", self.path)
            return
        with self._lock:
            for k, v in raw.items():
                if k in DEFAULT_CONFIG or True:
                    self._data[k] = v

    def save(self) -> None:
        with self._lock:
            data = dict(self._data)
            # 临时覆盖值不落盘：写回覆盖前的原值，保证 config.json 只反映用户真实设置
            for k, original in self._overridden.items():
                if original is _MISSING:
                    data.pop(k, None)
                else:
                    data[k] = original
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------- 访问 ----------

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def __getitem__(self, key: str):
        return self.get(key)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            # 用户显式设置过，就不再算临时覆盖
            self._overridden.pop(key, None)
        self.save()

    def override(self, key: str, value) -> None:
        """只改内存里的值，不写回磁盘，且后续 save() 也不会把它写进去。

        给命令行参数用：--save-root / --port 这类是一次性的临时覆盖，
        不该污染用户的 config.json（否则下次双击启动会莫名其妙沿用旧路径）。
        """
        with self._lock:
            if key not in self._overridden:
                self._overridden[key] = self._data.get(key, _MISSING)
            self._data[key] = value

    def update(self, patch: dict) -> None:
        with self._lock:
            self._data.update(patch)
            for k in patch:
                self._overridden.pop(k, None)
        self.save()

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._data)

    # ---------- 派生路径 ----------

    @property
    def save_root(self) -> Path:
        return Path(self.get("save_root"))

    @property
    def data_dir(self) -> Path:
        d = self.get("data_dir")
        if d:
            return Path(d)
        return Path(__file__).resolve().parent.parent / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "qqark.db"
