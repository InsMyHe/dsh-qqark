"""零依赖 WebSocket 服务端（RFC6455 子集）。

用途：NapCat 的 OneBot11 "反向 WebSocket" 会主动连到本机端口（默认 6200），
本模块作为服务端接受该连接，接收事件 JSON、回送 API 响应。

实现范围：
  - HTTP Upgrade 握手（Sec-WebSocket-Accept 校验计算）
  - 文本 / 二进制帧、分片重组、ping/pong、close 握手
  - 客户端帧掩码解码（浏览器与 NapCat 都会加掩码）
不支持：permessage-deflate 等扩展协商、子协议协商（按 RFC 允许不选）。

设计要点：每连接一个收包线程；发送走锁保护，避免多线程写同一 socket 撕裂帧。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import socket
import struct
import threading
import time

log = logging.getLogger("qqark.ws")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_FRAME = 64 * 1024 * 1024  # 单帧上限 64MB，防内存爆掉


class WSClosed(Exception):
    """连接已关闭或对端主动断开。"""


def _accept_key(key: str) -> str:
    sha1 = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(sha1).decode("ascii")


class WSConnection:
    """一条已握手的 WebSocket 连接。"""

    def __init__(self, sock: socket.socket, addr, path: str = "/", headers: dict | None = None):
        self.sock = sock
        self.addr = addr
        self.path = path
        self.headers = headers or {}
        self.alive = True
        self.created = time.time()
        self.last_recv = time.time()
        self.self_id = None  # 由上层填充：NapCat 的 X-Self-ID 头
        self.role = None     # 由上层填充：X-Client-Role
        self._send_lock = threading.Lock()
        self._buf = b""
        self.sock.settimeout(None)

    # ---------- 发送 ----------

    def _send_frame(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        header = bytearray()
        header.append((0x80 if fin else 0x00) | (opcode & 0x0F))
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        with self._send_lock:
            if not self.alive:
                raise WSClosed("connection closed")
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError as exc:
                self.alive = False
                raise WSClosed(str(exc)) from exc

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_json(self, obj) -> None:
        import json

        self.send_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    def ping(self, payload: bytes = b"") -> None:
        try:
            self._send_frame(OP_PING, payload)
        except WSClosed:
            pass

    # ---------- 接收 ----------

    def _read_exact(self, n: int) -> bytes:
        chunks = []
        got = 0
        while got < n:
            try:
                chunk = self.sock.recv(n - got)
            except OSError as exc:
                raise WSClosed(str(exc)) from exc
            if not chunk:
                raise WSClosed("peer closed")
            chunks.append(chunk)
            got += len(chunk)
        self.last_recv = time.time()
        return b"".join(chunks)

    def recv_message(self):
        """阻塞读取一条完整消息。

        返回 (opcode, data)。opcode 为 OP_TEXT / OP_BIN。
        收到 close 时返回 (OP_CLOSE, b"") 并关闭连接。
        ping 自动回 pong 后继续读。
        """
        frag_op = None
        frag_buf = bytearray()

        while True:
            b0, b1 = self._read_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if length > MAX_FRAME:
                raise WSClosed(f"frame too large: {length}")

            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(length) if length else b""
            if mask:
                payload = bytes(payload[i] ^ mask[i & 3] for i in range(len(payload)))

            if opcode == OP_CLOSE:
                try:
                    self._send_frame(OP_CLOSE, payload[:2])
                except WSClosed:
                    pass
                self.alive = False
                return (OP_CLOSE, b"")
            if opcode == OP_PING:
                try:
                    self._send_frame(OP_PONG, payload)
                except WSClosed:
                    return (OP_CLOSE, b"")
                continue
            if opcode == OP_PONG:
                continue

            if opcode == OP_CONT:
                if frag_op is None:
                    continue  # 非法续帧，忽略
                frag_buf.extend(payload)
            else:
                frag_op = opcode
                frag_buf = bytearray(payload)

            if fin:
                return (frag_op, bytes(frag_buf))

    # ---------- 关闭 ----------

    def close(self, code: int = 1000) -> None:
        if not self.alive:
            return
        self.alive = False
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", code))
        except WSClosed:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class WSServer:
    """极简 WS 服务端：accept 后握手，再按帧循环回调。

    回调均为可选：
      on_open(conn) -> bool | None   返回 False 可拒绝握手后的连接（会直接关闭）
      on_text(conn, text: str)
      on_close(conn, reason: str)
      on_headers(headers: dict) -> dict | None  额外响应头
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_open=None,
        on_text=None,
        on_close=None,
        extra_headers: dict | None = None,
    ):
        self.host = host
        self.port = port
        self.on_open = on_open
        self.on_text = on_text
        self.on_close = on_close
        self.extra_headers = extra_headers or {}
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.connections: list[WSConnection] = []
        self._conn_lock = threading.Lock()
        self.actual_port = port

    # ---------- 生命周期 ----------

    def start(self) -> None:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        srv = socket.socket(family, socket.SOCK_STREAM)
        # Windows 上 SO_REUSEADDR 语义是「允许抢占已被别的进程绑定的端口」，
        # 会导致 NapCat 把连接交给先绑定的那个进程，我们静默收不到任何事件。
        # 必须用 SO_EXCLUSIVEADDRUSE 保证「已被占用就绑定失败」，让错误显形。
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):      # Windows
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:                                            # POSIX
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(16)
        srv.settimeout(1.0)
        self._srv = srv
        self.actual_port = srv.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="ws-accept", daemon=True)
        self._thread.start()
        log.info("WebSocket 服务已监听 ws://%s:%s", self.host, self.actual_port)

    def stop(self) -> None:
        self._running = False
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass
        with self._conn_lock:
            conns = list(self.connections)
        for c in conns:
            c.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def broadcast_text(self, text: str) -> int:
        with self._conn_lock:
            conns = list(self.connections)
        n = 0
        for c in conns:
            try:
                c.send_text(text)
                n += 1
            except WSClosed:
                pass
        return n

    def client_count(self) -> int:
        with self._conn_lock:
            return len(self.connections)

    # ---------- 内部 ----------

    def _accept_loop(self) -> None:
        while self._running:
            try:
                sock, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(sock, addr), name=f"ws-{addr[1]}", daemon=True).start()

    def _handle(self, sock: socket.socket, addr) -> None:
        conn = None
        try:
            sock.settimeout(15.0)
            path, headers = self._read_handshake(sock)
            key = headers.get("sec-websocket-key")
            if not key:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return
            resp = [
                "HTTP/1.1 101 Switching Protocols",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Accept: {_accept_key(key)}",
            ]
            for k, v in self.extra_headers.items():
                resp.append(f"{k}: {v}")
            sock.sendall(("\r\n".join(resp) + "\r\n\r\n").encode("ascii"))

            conn = WSConnection(sock, addr, path, headers)
            conn.self_id = headers.get("x-self-id")
            conn.role = headers.get("x-client-role")
            sock.settimeout(None)

            with self._conn_lock:
                self.connections.append(conn)

            log.info("NapCat 已连接: %s self_id=%s role=%s path=%s",
                     addr, conn.self_id, conn.role, path)

            if self.on_open:
                try:
                    if self.on_open(conn) is False:
                        conn.close()
                        return
                except Exception:
                    log.exception("on_open 回调异常")

            while conn.alive and self._running:
                opcode, data = conn.recv_message()
                if opcode == OP_CLOSE:
                    break
                if opcode == OP_TEXT and self.on_text:
                    try:
                        self.on_text(conn, data.decode("utf-8", "replace"))
                    except Exception:
                        log.exception("on_text 回调异常")
        except WSClosed as exc:
            log.info("WS 连接结束 %s: %s", addr, exc)
        except Exception:
            log.exception("WS 连接异常 %s", addr)
        finally:
            if conn is not None:
                conn.alive = False
                with self._conn_lock:
                    if conn in self.connections:
                        self.connections.remove(conn)
                try:
                    conn.sock.close()
                except OSError:
                    pass
                if self.on_close:
                    try:
                        self.on_close(conn, "closed")
                    except Exception:
                        log.exception("on_close 回调异常")

    @staticmethod
    def _read_handshake(sock: socket.socket):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise WSClosed("handshake: peer closed")
            buf += chunk
            if len(buf) > 65536:
                raise WSClosed("handshake: header too large")
        head, _, _rest = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        request_line = lines[0] if lines else ""
        parts = request_line.split(" ")
        path = parts[1] if len(parts) >= 2 else "/"
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return path, headers
