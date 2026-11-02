"""SQLite 索引：记录每个抓到的文件，支持媒体库查询与去重。

表设计：
  files       — 抓取记录（主表）
  groups      — 群信息缓存（群名、成员数、监听开关）
  tasks       — 下载任务（实时事件 + 补扫产生），带状态机
  kv          — 杂项键值（补扫游标等）
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("qqark.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id     TEXT NOT NULL DEFAULT '',
    group_name   TEXT NOT NULL DEFAULT '',
    file_id      TEXT NOT NULL DEFAULT '',
    busid        INTEGER DEFAULT 0,
    file_name    TEXT NOT NULL DEFAULT '',
    save_path    TEXT UNIQUE,
    size         INTEGER DEFAULT 0,
    category     TEXT DEFAULT '其他',
    source       TEXT DEFAULT '群文件',
    uploader     TEXT DEFAULT '',
    uploader_id  TEXT DEFAULT '',
    msg_id       TEXT DEFAULT '',
    sha1         TEXT DEFAULT '',
    status       TEXT DEFAULT 'done',
    created_at   REAL DEFAULT 0,
    note         TEXT DEFAULT '',
    account      TEXT DEFAULT '',
    account_name TEXT DEFAULT '',
    dedupe_key   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_files_group   ON files(group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_cat     ON files(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_fid     ON files(file_id);
CREATE INDEX IF NOT EXISTS idx_files_size    ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_dedupe  ON files(group_id, dedupe_key);

CREATE TABLE IF NOT EXISTS groups (
    group_id     TEXT PRIMARY KEY,
    group_name   TEXT DEFAULT '',
    member_count INTEGER DEFAULT 0,
    enabled      INTEGER DEFAULT 1,
    last_scan    REAL DEFAULT 0,
    created_at   REAL DEFAULT 0,
    account      TEXT DEFAULT '',
    account_name TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS accounts (
    self_id      TEXT PRIMARY KEY,
    nickname     TEXT DEFAULT '',
    last_seen    REAL DEFAULT 0,
    created_at   REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    group_id     TEXT DEFAULT '',
    group_name   TEXT DEFAULT '',
    file_id      TEXT DEFAULT '',
    busid        INTEGER DEFAULT 0,
    file_name    TEXT DEFAULT '',
    size         INTEGER DEFAULT 0,
    url          TEXT DEFAULT '',
    uploader     TEXT DEFAULT '',
    uploader_id  TEXT DEFAULT '',
    msg_id       TEXT DEFAULT '',
    source       TEXT DEFAULT '群文件',
    state        TEXT DEFAULT 'pending',
    attempts     INTEGER DEFAULT 0,
    last_error   TEXT DEFAULT '',
    created_at   REAL DEFAULT 0,
    updated_at   REAL DEFAULT 0,
    account      TEXT DEFAULT '',
    account_name TEXT DEFAULT '',
    dedupe_key   TEXT DEFAULT '',
    UNIQUE(kind, group_id, file_id, file_name)
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_dedupe ON tasks(group_id, dedupe_key);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    def _migrate(self, c: sqlite3.Connection) -> None:
        """给老库补新列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
        want = {
            "files": ("account TEXT DEFAULT ''", "account_name TEXT DEFAULT ''",
                      "dedupe_key TEXT DEFAULT ''"),
            "tasks": ("account TEXT DEFAULT ''", "account_name TEXT DEFAULT ''",
                      "dedupe_key TEXT DEFAULT ''"),
            "groups": ("account TEXT DEFAULT ''", "account_name TEXT DEFAULT ''"),
        }
        for table, cols in want.items():
            try:
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
            for spec in cols:
                name = spec.split()[0]
                if name not in have:
                    try:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {spec}")
                        c.commit()
                        log.info("数据库迁移：%s 增加列 %s", table, name)
                    except sqlite3.Error as exc:
                        log.warning("数据库迁移失败 %s.%s: %s", table, name, exc)
        c.commit()

    def conn(self) -> sqlite3.Connection:
        """每线程一个连接（sqlite3 连接不可跨线程共享）。"""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30.0, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    # ---------- files ----------

    def add_file(self, **kw) -> int | None:
        cols = ("group_id", "group_name", "file_id", "busid", "file_name", "save_path",
                "size", "category", "source", "uploader", "uploader_id", "msg_id",
                "sha1", "status", "created_at", "note", "account", "account_name",
                "dedupe_key")
        data = {k: kw.get(k, "") for k in cols}
        data["created_at"] = kw.get("created_at") or time.time()
        sql = f"INSERT INTO files ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
        with self._write_lock:
            try:
                c = self.conn()
                cur = c.execute(sql, [data[k] for k in cols])
                c.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None
            except Exception:
                log.exception("写入索引失败")
                return None

    def file_exists(self, file_id: str, group_id: str) -> bool:
        """注意：file_id 在 NapCat 每次列举时都会变，别用它做去重判断。

        保留此方法只为兼容旧调用；新代码请用 dedupe_exists。
        """
        if not file_id:
            return False
        row = self.conn().execute(
            "SELECT 1 FROM files WHERE file_id=? AND group_id=? LIMIT 1", (file_id, group_id)
        ).fetchone()
        return row is not None

    def dedupe_exists(self, group_id: str, key: str, *, check_tasks: bool = True) -> bool:
        """这个文件是否已经在库里（下载完成）或已在队列里（待下载）。"""
        if not key:
            return False
        if self.conn().execute(
            "SELECT 1 FROM files WHERE group_id=? AND dedupe_key=? LIMIT 1", (group_id, key)
        ).fetchone():
            return True
        if check_tasks and self.conn().execute(
            "SELECT 1 FROM tasks WHERE group_id=? AND dedupe_key=? LIMIT 1", (group_id, key)
        ).fetchone():
            return True
        return False

    def size_exists(self, size: int, group_id: str) -> bool:
        if not size:
            return False
        row = self.conn().execute(
            "SELECT 1 FROM files WHERE size=? AND group_id=? LIMIT 1", (size, group_id)
        ).fetchone()
        return row is not None

    def query_files(
        self,
        *,
        q: str = "",
        group_id: str = "",
        category: str = "",
        source: str = "",
        media_only: bool = False,
        since: float = 0,
        until: float = 0,
        sort: str = "new",
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        where = ["status != 'deleted'"]
        args: list = []
        if q:
            where.append("(file_name LIKE ? OR uploader LIKE ? OR group_name LIKE ?)")
            like = f"%{q}%"
            args += [like, like, like]
        if group_id:
            where.append("group_id = ?")
            args.append(group_id)
        if category:
            where.append("category = ?")
            args.append(category)
        if source:
            where.append("source = ?")
            args.append(source)
        if media_only:
            where.append("category IN ('视频','图片','音频')")
        if since:
            where.append("created_at >= ?")
            args.append(since)
        if until:
            where.append("created_at <= ?")
            args.append(until)

        order = {
            "new": "created_at DESC",
            "old": "created_at ASC",
            "big": "size DESC",
            "small": "size ASC",
            "name": "file_name COLLATE NOCASE ASC",
        }.get(sort, "created_at DESC")

        clause = " WHERE " + " AND ".join(where)
        c = self.conn()
        total = c.execute(f"SELECT COUNT(*) FROM files{clause}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM files{clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*args, max(1, min(limit, 500)), max(0, offset)],
        ).fetchall()
        return [dict(r) for r in rows], total

    def stats(self) -> dict:
        c = self.conn()
        total = c.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE status='done'").fetchone()
        by_cat = c.execute(
            "SELECT category, COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
            "WHERE status='done' GROUP BY category ORDER BY n DESC"
        ).fetchall()
        by_group = c.execute(
            "SELECT group_id, group_name, COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
            "WHERE status='done' GROUP BY group_id ORDER BY n DESC LIMIT 100"
        ).fetchall()
        pending = c.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('pending','running')").fetchone()[0]
        failed = c.execute("SELECT COUNT(*) FROM tasks WHERE state='failed'").fetchone()[0]
        # 「群里已经没有了」单独一桶：它既不该算进失败（那会让人一直以为还有救），
        # 也不该算进待下载（那会让进度条永远不满）。见 downloader._Gone。
        gone = c.execute("SELECT COUNT(*) FROM tasks WHERE state='gone'").fetchone()[0]
        return {
            "total_files": total[0],
            "total_size": total[1],
            "by_category": [dict(r) for r in by_cat],
            "by_group": [dict(r) for r in by_group],
            "pending_tasks": pending,
            "failed_tasks": failed,
            "gone_tasks": gone,
        }

    def delete_file_row(self, fid: int) -> dict | None:
        with self._write_lock:
            c = self.conn()
            row = c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
            if not row:
                return None
            c.execute("DELETE FROM files WHERE id=?", (fid,))
            c.commit()
            return dict(row)

    # ---------- groups ----------

    def upsert_group(self, group_id: str, group_name: str = "", member_count: int = 0,
                     account: str = "", account_name: str = "") -> None:
        with self._write_lock:
            c = self.conn()
            c.execute(
                "INSERT INTO groups (group_id, group_name, member_count, created_at, account, account_name) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET "
                "group_name=CASE WHEN excluded.group_name!='' THEN excluded.group_name ELSE groups.group_name END,"
                "member_count=CASE WHEN excluded.member_count>0 THEN excluded.member_count ELSE groups.member_count END,"
                "account=CASE WHEN excluded.account!='' THEN excluded.account ELSE groups.account END,"
                "account_name=CASE WHEN excluded.account_name!='' THEN excluded.account_name ELSE groups.account_name END",
                (str(group_id), group_name, int(member_count or 0), time.time(),
                 str(account or ""), str(account_name or "")),
            )
            c.commit()

    def touch_account(self, self_id: str, nickname: str = "") -> None:
        if not self_id:
            return
        with self._write_lock:
            c = self.conn()
            c.execute(
                "INSERT INTO accounts (self_id, nickname, last_seen, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(self_id) DO UPDATE SET "
                "nickname=CASE WHEN excluded.nickname!='' THEN excluded.nickname ELSE accounts.nickname END,"
                "last_seen=excluded.last_seen",
                (str(self_id), nickname or "", time.time(), time.time()),
            )
            c.commit()

    def list_accounts(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT a.*, (SELECT COUNT(*) FROM groups g WHERE g.account=a.self_id) AS group_count "
            "FROM accounts a ORDER BY a.last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def group_account_map(self) -> dict[str, str]:
        rows = self.conn().execute("SELECT group_id, account FROM groups").fetchall()
        return {r["group_id"]: (r["account"] or "") for r in rows}

    def list_groups(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT g.*, (SELECT COUNT(*) FROM files f WHERE f.group_id=g.group_id AND f.status='done') AS file_count "
            "FROM groups g ORDER BY g.group_name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_group_enabled(self, group_id: str, enabled: bool) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute(
                "INSERT INTO groups (group_id, enabled, created_at) VALUES (?,?,?) "
                "ON CONFLICT(group_id) DO UPDATE SET enabled=excluded.enabled",
                (str(group_id), 1 if enabled else 0, time.time()),
            )
            c.commit()

    def group_enabled_map(self) -> dict[str, bool]:
        rows = self.conn().execute("SELECT group_id, enabled FROM groups").fetchall()
        return {r["group_id"]: bool(r["enabled"]) for r in rows}

    def mark_scanned(self, group_id: str) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute("UPDATE groups SET last_scan=? WHERE group_id=?", (time.time(), str(group_id)))
            c.commit()

    # ---------- tasks ----------

    def add_task(self, **kw) -> int | None:
        cols = ("kind", "group_id", "group_name", "file_id", "busid", "file_name", "size",
                "url", "uploader", "uploader_id", "msg_id", "source", "state",
                "attempts", "last_error", "created_at", "updated_at",
                "account", "account_name", "dedupe_key")
        now = time.time()
        data = {k: kw.get(k, "") for k in cols}
        data["created_at"] = kw.get("created_at") or now
        data["updated_at"] = now
        data["state"] = kw.get("state", "pending")
        key = str(data.get("dedupe_key") or "")
        gid = str(data.get("group_id") or "")
        with self._write_lock:
            c = self.conn()
            # 老库的 UNIQUE 约束含 file_id，而 file_id 每次都变，拦不住重复；
            # 这里按 dedupe_key 显式查一遍，保证同一文件只入队一次。
            if key and c.execute(
                "SELECT 1 FROM tasks WHERE group_id=? AND dedupe_key=? LIMIT 1", (gid, key)
            ).fetchone():
                return None
            sql = f"INSERT OR IGNORE INTO tasks ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
            cur = c.execute(sql, [data[k] for k in cols])
            c.commit()
            return cur.lastrowid if cur.rowcount else None

    def next_tasks(self, limit: int = 10) -> list[dict]:
        rows = self.conn().execute(
            "SELECT * FROM tasks WHERE state='pending' ORDER BY created_at ASC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def claim_task(self, tid: int) -> bool:
        with self._write_lock:
            c = self.conn()
            cur = c.execute(
                "UPDATE tasks SET state='running', updated_at=? WHERE id=? AND state='pending'",
                (time.time(), tid),
            )
            c.commit()
            return cur.rowcount > 0

    def finish_task(self, tid: int, state: str, error: str = "") -> None:
        with self._write_lock:
            c = self.conn()
            c.execute(
                "UPDATE tasks SET state=?, last_error=?, updated_at=?, attempts=attempts+1 WHERE id=?",
                (state, error[:500], time.time(), tid),
            )
            c.commit()

    def retry_task(self, tid: int) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute("UPDATE tasks SET state='pending', updated_at=? WHERE id=?", (time.time(), tid))
            c.commit()

    def revive_gone(self, group_id: str, dedupe_key_value: str) -> bool:
        """补扫又在群里看到了这个文件 —— 把之前判死的 gone 任务放回队列。

        为什么需要这一步：`gone` 是「整棵目录树里没有这个文件名」推出来的
        结论，而列目录接口**可能分页**（一次只给一部分）。万一标准是错的，
        `gone` 就成了不可逆的误判、用户真正想要的文件永远下不来。
        与其假定接口不分页，不如让它可自愈：只要补扫看到它，就重新排队。
        """
        with self._write_lock:
            c = self.conn()
            cur = c.execute(
                "UPDATE tasks SET state='pending', last_error='', attempts=0, updated_at=? "
                "WHERE group_id=? AND dedupe_key=? AND state='gone'",
                (time.time(), str(group_id), dedupe_key_value),
            )
            c.commit()
            return cur.rowcount > 0

    def update_task_file_ref(self, tid: int, file_id: str, busid: int | None = None) -> None:
        """刷新任务里的 file_id / busid。

        NapCat 每次列举群文件都会重新生成 file_id，库里存的那份过一段时间就
        换不到链接了（retcode=1200 real fileUUID not found!）。现查到新的之后
        写回来，后面的重试就不用再扫一遍目录。
        """
        with self._write_lock:
            c = self.conn()
            if busid is None:
                c.execute("UPDATE tasks SET file_id=?, updated_at=? WHERE id=?",
                          (file_id, time.time(), tid))
            else:
                c.execute("UPDATE tasks SET file_id=?, busid=?, updated_at=? WHERE id=?",
                          (file_id, int(busid), time.time(), tid))
            c.commit()

    def count_tasks(self, state: str) -> int:
        return self.conn().execute("SELECT COUNT(*) FROM tasks WHERE state=?", (state,)).fetchone()[0]

    def recent_tasks(self, limit: int = 30, states: tuple = ()) -> list[dict]:
        c = self.conn()
        if states:
            ph = ",".join("?" for _ in states)
            rows = c.execute(
                f"SELECT * FROM tasks WHERE state IN ({ph}) ORDER BY updated_at DESC LIMIT ?",
                (*states, limit),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reset_stuck_tasks(self) -> int:
        """进程重启时把上次没跑完的 running 任务放回队列。"""
        with self._write_lock:
            c = self.conn()
            cur = c.execute("UPDATE tasks SET state='pending', updated_at=? WHERE state='running'", (time.time(),))
            c.commit()
            return cur.rowcount

    # ---------- kv ----------

    def kv_get(self, k: str, default=None):
        row = self.conn().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["v"])
        except Exception:
            return default

    def kv_set(self, k: str, v) -> None:
        with self._write_lock:
            c = self.conn()
            c.execute(
                "INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v, ensure_ascii=False)),
            )
            c.commit()
