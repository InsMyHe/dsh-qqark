"""NapCat 配置检测与自动修复。

用户最容易卡住的地方：NapCat 里没配反向 WS，软件就永远等不到连接。
这个模块负责：
  1. 找到 NapCat 的配置目录（多个安装位置都试）
  2. 读 onebot11_<QQ号>.json，判断是否已指向我们的 ws://127.0.0.1:6200/ws
  3. 没有就追加一条（备份原文件），已有则一行不动

注意：NapCat 支持同时连多个反向 WS 客户端。6199 通常是 AstrBot 的
aiocqhttp 端口，我们**不抢占**，只追加自己的 6200，两边并存。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("qqark.napcat")

# 常见的 NapCat 安装/配置位置（按可能性排序）
SEARCH_ROOTS = [
    Path(os.environ.get("USERPROFILE", "")) / "Downloads",
    Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    Path(os.environ.get("USERPROFILE", "")) / "Documents",
    Path("C:/NapCat"),
    Path("C:/Program Files/NapCat"),
    Path("D:/NapCat"),
    Path("D:/"),
]

CONFIG_SUBPATH = Path("resources/app/napcat/config")


def find_napcat_dirs() -> list[Path]:
    """返回所有找到的 napcat/config 目录。"""
    found: list[Path] = []

    # 1) 从运行中的进程反推（最准）
    try:
        import subprocess

        out = subprocess.run(
            ["wmic", "process", "where", "name='QQ.exe'", "get", "ExecutablePath", "/format:list"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        for line in out.splitlines():
            line = line.strip()
            if not line.lower().startswith("executablepath="):
                continue
            exe = Path(line.split("=", 1)[1].strip())
            if not exe.name:
                continue
            # QQ.exe 在 .../versions/<ver>/resources/app/... 或同级；向上找 napcat/config
            for parent in [exe.parent, *exe.parents]:
                cand = parent / CONFIG_SUBPATH
                if cand.is_dir():
                    found.append(cand)
                    break
    except Exception:
        pass

    # 2) 扫常见目录
    for root in SEARCH_ROOTS:
        if not root or not root.exists():
            continue
        try:
            for depth_root in _walk_limited(root, max_depth=4):
                cand = depth_root / CONFIG_SUBPATH
                if cand.is_dir() and cand not in found:
                    found.append(cand)
        except (OSError, PermissionError):
            continue

    # 去重并保持顺序
    uniq: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in [u.resolve() for u in uniq]:
            uniq.append(p)
    return uniq


def _walk_limited(root: Path, max_depth: int):
    root_depth = len(root.parts)
    for cur, dirs, _files in os.walk(root, onerror=lambda e: None):
        p = Path(cur)
        if len(p.parts) - root_depth > max_depth:
            dirs[:] = []
            continue
        # 跳过明显无关的大目录，避免卡死
        dirs[:] = [d for d in dirs if d.lower() not in (
            "node_modules", ".git", "__pycache__", "cache", "temp", "tmp",
            "winsxs", "appdata", "system32",
        )]
        yield p


def read_onebot_configs(config_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    try:
        for f in sorted(config_dir.glob("onebot11_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8-sig"))
                out.append((f, data))
            except Exception as exc:
                log.warning("配置文件无法解析 %s: %s", f, exc)
    except OSError:
        pass
    return out


def _clients_container(data: dict) -> tuple[dict, str]:
    """NapCat 版本之间配置层级不同：新版在 network 下，老版在顶层。

    返回 (持有 websocketClients 的字典, 键名)。
    """
    net = data.get("network")
    if isinstance(net, dict):
        for key in ("websocketClients", "wsClients"):
            if isinstance(net.get(key), list):
                return net, key
        # network 存在但没有该键 → 就在 network 里补
        return net, "websocketClients"
    for key in ("websocketClients", "wsClients"):
        if isinstance(data.get(key), list):
            return data, key
    return data, "websocketClients"


def _get_clients(data: dict) -> list:
    holder, key = _clients_container(data)
    v = holder.get(key)
    return v if isinstance(v, list) else []


def check_connection(data: dict, host: str = "127.0.0.1", port: int = 6200,
                     path: str = "/ws") -> tuple[bool, str]:
    """判断这份 onebot 配置能否连到本软件。"""
    clients = _get_clients(data)
    if not clients:
        return False, "没有配置反向 WebSocket 客户端"

    want = f"ws://{host}:{port}{path}"
    candidates = []
    for c in clients:
        if not isinstance(c, dict):
            continue
        if not c.get("enable", True):
            continue
        url = str(c.get("url") or "").strip()
        if not url:
            continue
        candidates.append(url)
        if _url_matches(url, host, port, path):
            fmt = c.get("messagePostFormat") or ""
            tip = "" if fmt == "array" else "（建议 messagePostFormat 设为 array）"
            return True, f"已指向 {url}{tip}"

    if candidates:
        return False, f"已配置的地址都不匹配，当前为 {candidates[0]}，期望 {want}"
    return False, f"反向 WS 客户端都未启用，期望 {want}"


def _url_matches(url: str, host: str, port: int, path: str) -> bool:
    u = url.lower().replace("ws://", "").replace("wss://", "")
    hostpart, _, pathpart = u.partition("/")
    pathpart = "/" + pathpart if pathpart else "/"
    want_path = path or "/ws"
    if pathpart.rstrip("/") != want_path.rstrip("/"):
        return False
    h, _, p = hostpart.partition(":")
    port_ok = p == str(port)
    host_ok = h in (host, "localhost", "127.0.0.1", "0.0.0.0") or host in (h,)
    return port_ok and host_ok


def build_fixed_client(port: int = 6200, path: str = "/ws") -> dict:
    return {
        "enable": True,
        "name": "qqark",
        "url": f"ws://127.0.0.1:{port}{path}",
        "messagePostFormat": "array",
        "reportSelfMessage": False,
        "token": "",
        "debug": False,
        "heartInterval": 1000,
        "reconnectInterval": 1000,
    }


def apply_fix(config_file: Path, data: dict, port: int = 6200, path: str = "/ws") -> bool:
    """往 NapCat 里追加一条指向本软件的反向 WS 客户端。

    只追加，绝不删改已有条目——6199 上通常跑着 AstrBot，
    NapCat 支持同时连多个客户端，两边并存才是正确做法。
    """
    backup = config_file.with_suffix(config_file.suffix + f".bak{int(time.time())}")
    try:
        shutil.copy2(config_file, backup)
    except OSError as exc:
        log.error("备份失败，放弃修改: %s", exc)
        return False

    holder, key = _clients_container(data)
    clients = holder.get(key)
    if not isinstance(clients, list):
        clients = []

    for c in clients:
        if isinstance(c, dict) and _url_matches(str(c.get("url") or ""), "127.0.0.1", port, path):
            log.info("已有指向本软件的客户端，无需修改: %s", config_file.name)
            return True

    clients.append(build_fixed_client(port, path))
    holder[key] = clients

    try:
        config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("已更新 %s（备份：%s）", config_file, backup.name)
        return True
    except OSError as exc:
        log.error("写入失败: %s", exc)
        return False


# ---------- WebUI 备用查询 ----------
#
# OneBot 的 get_login_info 在 NapCat 9.9.33 上会一直不返回（实测超时 60s）。
# 昵称走 WebUI 的 /api/QQLogin/GetQQLoginInfo 才拿得到。这里做一个只读的
# 最小客户端：登录拿 Credential，然后调一个接口。全程只 GET/POST 读操作。


def read_webui_config(config_dir: Path) -> dict:
    """读 webui.json，返回 {host, port, token, disableWebUI}。"""
    f = config_dir / "webui.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    host = str(data.get("host") or "127.0.0.1")
    if host in ("::", "0.0.0.0", ""):
        host = "127.0.0.1"
    return {
        "host": host,
        "port": int(data.get("port") or 6099),
        "token": str(data.get("token") or ""),
        "disabled": bool(data.get("disableWebUI")),
    }


def webui_login(host: str, port: int, token: str, timeout: float = 5) -> str:
    """用 webui.json 里的 token 换 Bearer Credential。"""
    hash_hex = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    body = json.dumps({"hash": hash_hex, "totpCode": ""}).encode("utf-8")
    req = urllib.request.Request(f"http://{host}:{port}/api/auth/login",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return str(((data.get("data") or {}).get("Credential")) or "")


def webui_post(host: str, port: int, cred: str, api: str,
               payload: dict | None = None, timeout: float = 5) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(f"http://{host}:{port}/api/{api}",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {cred}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def query_login_info_via_webui(config_dirs: list[Path],
                               timeout: float = 4) -> dict | None:
    """尽最大努力从 NapCat WebUI 拿到 {uin, nick}，拿不到返回 None。

    只读：只调 auth/login 与 QQLogin/GetQQLoginInfo。
    """
    for d in config_dirs:
        wc = read_webui_config(d)
        if not wc or wc.get("disabled"):
            continue
        try:
            cred = webui_login(wc["host"], wc["port"], wc["token"], timeout)
            if not cred:
                continue
            resp = webui_post(wc["host"], wc["port"], cred,
                              "QQLogin/GetQQLoginInfo", {}, timeout)
            data = resp.get("data") or {}
            if data.get("uin"):
                return {"uin": str(data["uin"]), "nick": str(data.get("nick") or "")}
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            continue
    return None


# ---------- CLI ----------

def main_check(auto_fix: bool = False) -> int:
    port = 6200
    path = "/ws"
    # 允许从配置里读端口
    try:
        cfg_file = Path(__file__).resolve().parent.parent / "config.json"
        if cfg_file.exists():
            c = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            port = int(c.get("ws_port") or port)
            path = c.get("ws_path") or path
    except Exception:
        pass

    print()
    print("  检查 NapCat 配置…")
    print()

    dirs = find_napcat_dirs()
    if not dirs:
        print("  ✗ 没找到 NapCat 配置目录。")
        print("    请确认 NapCat 已安装并至少启动过一次。")
        print("    手动位置通常在：<NapCat目录>/versions/<版本>/resources/app/napcat/config")
        return 2

    print(f"  找到 {len(dirs)} 个配置目录：")
    for d in dirs:
        print(f"    · {d}")
    print()

    ok_any = False
    todo: list[tuple[Path, dict]] = []
    for d in dirs:
        entries = read_onebot_configs(d)
        if not entries:
            print(f"  ⚠ {d.name} 下没有 onebot11_*.json（NapCat 可能还没登录过 QQ）")
            continue
        for f, data in entries:
            qq = f.stem.replace("onebot11_", "")
            ok, msg = check_connection(data, port=port, path=path)
            mark = "✓" if ok else "✗"
            print(f"  {mark} QQ {qq}: {msg}")
            if ok:
                ok_any = True
            else:
                todo.append((f, data))
        print()

    if ok_any:
        print("  配置没问题。启动软件后 NapCat 会自动连上来。")
        return 0

    if not todo:
        return 2

    print("  需要修改以下文件的「网络配置 → WebSocket 客户端」：")
    for f, _ in todo:
        print(f"    · {f}")
    print()
    print(f"  目标地址： ws://127.0.0.1:{port}{path}")
    print("  消息格式： array")
    print()

    if auto_fix:
        _apply_all(todo, port, path)
        return 0

    try:
        ans = input("  要自动改吗？会先备份原文件 [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes"):
        _apply_all(todo, port, path)
        return 0

    print("  未修改。你也可以在 NapCat WebUI（默认 http://127.0.0.1:6099）里手动添加。")
    return 0


def _apply_all(todo, port: int, path: str) -> None:
    """批量改配置，并如实报告每个文件的结果——失败绝不能报成功。"""
    done, failed = [], []
    for f, data in todo:
        (done if apply_fix(f, data, port=port, path=path) else failed).append(f)

    if done:
        print(f"  已修改 {len(done)} 个文件。请**重启 NapCat** 使其生效。")
    if failed:
        print()
        print("  以下文件改不了（通常是权限问题）：")
        for f in failed:
            print(f"    · {f}")
        print()
        print("  可以这样处理：")
        print("    1. 完全退出 QQ 与 NapCat，再运行本检查")
        print("    2. 或者用管理员权限的终端运行")
        print("    3. 或者打开 NapCat WebUI → 网络配置 → WebSocket 客户端 → 新增：")
        print(f"         地址 ws://127.0.0.1:{port}{path}   消息格式 array")



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main_check("--fix" in sys.argv))
