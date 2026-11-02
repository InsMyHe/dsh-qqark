"""事件路由：把 OneBot 事件翻译成下载任务。

处理三类来源：
  1) notice / group_upload            群文件被上传（最可靠，秒级）
  2) message / group 里的 video 段    聊天里直接发的视频（链接 rkey 短命，要立刻下）
  3) message / group 里的 file 段     聊天里直接发的文件

群消息里的文件/视频段结构（NapCat，messagePostFormat=array）：
  {"type":"video","data":{"file":"xxx.mp4","url":"https://...","file_size":"12345","path":""}}
  {"type":"file","data":{"file":"xxx.zip","file_id":"/abc","file_size":"12345","busid":"102"}}
视频有时只有 file 名没有 url，需要用 get_file(file_id) 换 url；消息段的 file 字段即 file_id。
"""

from __future__ import annotations

import logging
import re
import time

from .classify import category_of, dedupe_key
from .config import Config
from .db import Database
from .downloader import Downloader
from .onebot import OneBotClient, OneBotError

log = logging.getLogger("qqark.events")


class EventRouter:
    def __init__(self, cfg: Config, db: Database, ob: OneBotClient, dl: Downloader):
        self.cfg = cfg
        self.db = db
        self.ob = ob
        self.dl = dl
        self._group_names: dict[str, str] = {}
        self._group_account: dict[str, str] = {}
        self.counters = {"group_upload": 0, "chat_video": 0, "chat_file": 0, "ignored": 0}

    # ---------- 群名缓存 ----------

    def group_name(self, group_id: str, self_id: str = "") -> str:
        group_id = str(group_id or "")
        if not group_id:
            return ""
        name = self._group_names.get(group_id)
        if name:
            return name
        try:
            info = self.ob.get_group_info(group_id, self_id=self_id or None)
            name = info.get("group_name") or info.get("group_name_raw") or ""
            count = int(info.get("member_count") or 0)
            if name:
                self._group_names[group_id] = name
            self.db.upsert_group(group_id, name, count, account=self_id,
                                 account_name=self.ob._nicknames.get(str(self_id), ""))
            return name
        except Exception as exc:
            log.debug("获取群名失败 %s: %s", group_id, exc)
            return ""

    def refresh_groups(self, self_id: str = "") -> list[dict]:
        """从指定账号（或全部账号）拉群列表并写库。"""
        if self_id:
            accounts = [self_id]
        else:
            accounts = [a["self_id"] for a in self.ob.accounts()] or [""]
        out: list[dict] = []
        for acc in accounts:
            try:
                groups = self.ob.get_group_list(self_id=acc or None)
            except OneBotError as exc:
                log.warning("获取群列表失败（账号 %s）: %s", acc or "-", exc)
                continue
            nick = self.ob._nicknames.get(str(acc), "") if acc else ""
            for g in groups:
                gid = str(g.get("group_id") or g.get("group_id_raw") or "")
                if not gid:
                    continue
                name = g.get("group_name") or ""
                self._group_names[gid] = name
                if acc:
                    self._group_account[gid] = str(acc)
                self.db.upsert_group(gid, name, int(g.get("member_count") or 0),
                                     account=str(acc or ""), account_name=nick)
                out.append({"group_id": gid, "group_name": name, "account": str(acc or "")})
            log.info("账号 %s 的群列表已刷新，共 %d 个群", acc or "-", len(groups))
        log.info("群列表已刷新，共 %d 个群（%d 个账号）", len(out), len(accounts))
        return out

    # ---------- 过滤 ----------

    def _wanted(self, group_id: str) -> bool:
        if not self.db_group_enabled(group_id):
            return False
        watch = self.cfg.get("watch_groups") or []
        if watch and str(group_id) not in {str(w) for w in watch}:
            return False
        return True

    def db_group_enabled(self, group_id: str) -> bool:
        m = self.db.group_enabled_map()
        return m.get(str(group_id), True)

    # ---------- 主入口 ----------

    def handle(self, event: dict, self_id: str = "") -> None:
        try:
            post_type = event.get("post_type")
            if post_type == "notice":
                # 顺便学习群名
                if event.get("group_id"):
                    self._maybe_group(event["group_id"], self_id)
                self._handle_notice(event, self_id)
            elif post_type == "message":
                self._handle_message(event, self_id)
            elif post_type == "meta_event":
                if event.get("meta_event_type") == "lifecycle":
                    log.info("NapCat 生命周期事件: %s（账号 %s）", event.get("sub_type"), self_id)
                    self._refresh_self(self_id)
        except Exception:
            log.exception("事件处理失败: %.300s", str(event)[:300])

    def _maybe_group(self, gid, self_id: str = "") -> None:
        gid = str(gid)
        if gid not in self._group_names:
            # 不在事件线程里同步等 API，避免阻塞心跳；交给后台线程
            import threading

            threading.Thread(target=self.group_name, args=(gid, self_id), daemon=True).start()

    def _refresh_self(self, self_id: str = "") -> None:
        # 昵称只是装饰，NapCat 某些版本 get_login_info 会挂起，
        # 这里超时短一点，失败就当没有昵称，不能影响主流程。
        try:
            info = self.ob.call("get_login_info", {}, 8, self_id=self_id or None)
            nick = (info or {}).get("nickname") or ""
            sid = str((info or {}).get("user_id") or self_id or "")
            if nick:
                self.ob.set_nickname(sid, nick)
            if sid:
                self.db.touch_account(sid, nick)
        except Exception:
            if self_id:
                try:
                    self.db.touch_account(self_id, "")
                except Exception:
                    pass

    # ---------- 通知事件 ----------

    def _handle_notice(self, event: dict, self_id: str = "") -> None:
        if event.get("notice_type") != "group_upload":
            self.counters["ignored"] += 1
            return
        if not self.cfg.get("grab_group_files"):
            return

        group_id = str(event.get("group_id") or "")
        if not group_id or not self._wanted(group_id):
            return

        f = event.get("file") or {}
        file_name = f.get("name") or f.get("file_name") or ""
        file_id = f.get("id") or f.get("file_id") or ""
        size = int(f.get("size") or f.get("file_size") or 0)
        busid = int(f.get("busid") or 0)
        if not file_name:
            return

        self.counters["group_upload"] += 1
        name = self.group_name(group_id, self_id)
        uploader_id2 = str(event.get("user_id") or "")
        log.info("群文件上传: [%s] %s (%.2f MB)", name or group_id, file_name, size / 1048576)

        self.dl.enqueue(
            kind="group_file",
            group_id=group_id,
            group_name=name,
            file_id=file_id,
            busid=busid,
            file_name=file_name,
            size=size,
            uploader=uploader_id2,
            uploader_id=uploader_id2,
            source="群文件",
            account=str(self_id or ""),
            account_name=self.ob._nicknames.get(str(self_id), ""),
            dedupe_key=dedupe_key(group_id, file_name, size),
        )

    # ---------- 消息事件 ----------

    def _handle_message(self, event: dict, self_id: str = "") -> None:
        if event.get("message_type") != "group":
            self.counters["ignored"] += 1
            return
        group_id = str(event.get("group_id") or "")
        if not group_id or not self._wanted(group_id):
            return

        segments = event.get("message")
        if isinstance(segments, str):
            return  # 字符串格式解析成本高且 NapCat 已配 array，跳过
        if not isinstance(segments, list):
            return

        sender = event.get("sender") or {}
        uploader = sender.get("card") or sender.get("nickname") or ""
        uploader_id = str(sender.get("user_id") or event.get("user_id") or "")
        msg_id = str(event.get("message_id") or "")
        ts = float(event.get("time") or time.time())

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            stype = seg.get("type")
            data = seg.get("data") or {}
            if stype == "video":
                if self.cfg.get("grab_chat_video"):
                    self._enqueue_chat(seg_type="video", data=data, group_id=group_id,
                                       uploader=uploader, uploader_id=uploader_id,
                                       msg_id=msg_id, ts=ts, self_id=self_id)
            elif stype == "file":
                if self.cfg.get("grab_chat_file"):
                    self._enqueue_chat(seg_type="file", data=data, group_id=group_id,
                                       uploader=uploader, uploader_id=uploader_id,
                                       msg_id=msg_id, ts=ts, self_id=self_id)
            elif stype == "image":
                if self.cfg.get("grab_images"):
                    self._enqueue_chat(seg_type="image", data=data, group_id=group_id,
                                       uploader=uploader, uploader_id=uploader_id,
                                       msg_id=msg_id, ts=ts, self_id=self_id)

    def _enqueue_chat(self, *, seg_type: str, data: dict, group_id: str, uploader: str,
                      uploader_id: str, msg_id: str, ts: float, self_id: str = "") -> None:
        raw_name = data.get("file") or data.get("file_name") or data.get("name") or ""
        file_id = data.get("file_id") or data.get("id") or data.get("file") or ""
        size = 0
        for key in ("file_size", "size"):
            try:
                size = int(data.get(key) or 0)
                if size:
                    break
            except (TypeError, ValueError):
                continue
        url = data.get("url") or ""
        # 聊天消息段里的 url 也不一定是链接：NapCat 在某些情况下会给本地缓存
        # 路径（例如 packetBackend 挂了，或图片视频还没上传完）。这类值让 urllib
        # 抛 `unknown url type: c`，所以只留 http/https，其余当没有，让下载器
        # 走 get_file 现换链接。
        if url and not str(url).lower().startswith(("http://", "https://")):
            log.debug("聊天消息段里的 url 不是链接，丢弃：%s", str(url)[:120])
            url = ""

        # 文件名可能没有扩展名（视频常见 hex 名），补一个
        file_name = raw_name or f"{seg_type}_{msg_id or int(ts)}"
        if "." not in file_name:
            file_name = f"{file_name}.{_default_ext(seg_type)}"

        cat = category_of(file_name)
        source = {"video": "聊天视频", "file": "聊天文件", "image": "聊天图片"}[seg_type]
        log.info("%s: [%s] %s %s", source, group_id, file_name,
                 f"({size / 1048576:.2f} MB)" if size else "")

        if seg_type == "video":
            self.counters["chat_video"] += 1
        elif seg_type == "file":
            self.counters["chat_file"] += 1

        name = self.group_name(group_id, self_id)
        self.dl.enqueue(
            kind="chat",
            group_id=group_id,
            group_name=name,
            file_id=file_id,
            busid=int(data.get("busid") or 0),
            file_name=file_name,
            size=size,
            url=url,
            uploader=uploader,
            uploader_id=uploader_id,
            msg_id=msg_id,
            source=source,
            created_at=ts,
            account=str(self_id or ""),
            account_name=self.ob._nicknames.get(str(self_id), ""),
            dedupe_key=dedupe_key(group_id, file_name, size),
        )


def _default_ext(seg_type: str) -> str:
    return {"video": "mp4", "image": "jpg", "file": "bin"}.get(seg_type, "bin")
