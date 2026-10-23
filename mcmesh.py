#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCMesh — 我的世界（Java / Fabric 整合包）联机存档同步助手

思路：把"整份存档"当 Git 仓库的内容来管。

   开玩前：pull   拉最新存档 -> 备份本地 -> 覆盖本地
   玩完后：push   把本地存档传上去，提交信息里记着"谁、什么时候"

两种模式：
   lan     —— 继续用"对局域网开放"的单人存档（零服务端，换人会继承房主角色）
   server  —— Fabric 专用服，轮流在自己电脑上开服（所有人平权，身份互不干扰）

常用：
   python mcmesh.py            # 中文菜单
   python mcmesh.py doctor     # 自助体检
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

def app_dir() -> Path:
    """程序所在的"工作目录"。

    打包成 exe 之后 `__file__` 指向临时解包目录（_MEIPASS），配置/备份必须放在 exe 旁边，
    否则一关程序就没了。所以这里按有没有 frozen 标记区分。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    r"""配置/状态放这儿：%USERPROFILE%\.mcmesh —— 不跟 exe 走。

    为什么：exe 可能被放在只读目录、或者在压缩包里直接双击（会临时解包到 %TEMP%），
    "存在 exe 旁边"的配置下次就找不到了 —— 用户会看到"每次都要重新填"。
    """
    d = Path.home() / ".mcmesh"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return TOOL_DIR


TOOL_DIR = app_dir()
DATA_DIR = data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"
BACKUP_DIR = DATA_DIR / "backups"          # 备份也放用户目录，桌面/程序目录保持干净
FABRIC_META = "https://meta.fabricmc.net/v2"

# 这些文件不属于"世界内容"，同步时跳过
WORLD_IGNORE = {"session.lock", ".DS_Store", "Thumbs.db", "desktop.ini"}

DEFAULT_CONFIG = {
    "player": "",            # 你在这个世界里的名字（离线模式靠它认人，别乱改）
    "repo_url": "",          # GitHub 私有仓库地址
    "branch": "main",
    "mode": "lan",           # lan | server
    "instance": "",          # 整合包实例目录（含 mods/ config/ saves/）
    "world": "BetterMC",     # 存档文件夹名（mode=lan 时是 saves/ 下的名字）
    "mc_version": "1.21.1",
    "fabric_loader": "",     # 留空=自动选稳定版
    "server_memory": "6G",
    "server_port": 25565,
    "launcher_exe": "",      # 启动器 exe，留空则只提示手动打开
    "work_dir": "",          # 仓库本地目录，留空=工具目录下的 repo/
    "server_mods_exclude": [],   # 额外排除的 mod（文件名关键字）
    "server_mods_include": [],   # 强制保留的 mod
}

# fabric.mod.json 里没写 environment（默认 *）但其实只能在客户端跑的常见 mod
CLIENT_ONLY_HINTS = [
    "sodium", "iris", "bobby", "citresewn", "zoomify", "modmenu",
    "reeses-sodium-options", "sodium-extra", "sodiumextras", "sodiumdynamiclights",
    "sodiumleafculling", "sodiumoptionsapi", "sodiumoptionsmodcompat", "bettergrassify",
    "notenoughanimations", "skinlayers", "visuality", "nicer-skies", "particle-effects",
    "particle_core", "sound-physics", "xaero", "chat_heads", "no-report-button",
    "fancymenu", "melody", "discord", "audio improvements", "satisfying_buttons",
    "blended-compat", "mapdistancefix", "gpumemleakfix", "clientsort", "clientcrafting",
]


# ────────────────────────────────────────────────── 输出与基础工具

def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    print(f"[OK] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[! ] {msg}", flush=True)


def info(msg: str) -> None:
    print(f"     {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"[X ] {msg}", flush=True)
    sys.exit(code)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# Windows：从 pythonw 里起子进程时，不给新建控制台窗口（否则每调一次闪一个黑框）
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run(cmd, cwd=None, check=True, capture=True):
    """跑子进程；cmd 用列表，避免路径里的空格/方括号出问题。"""
    args = [str(c) for c in cmd]
    try:
        p = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        die(f"找不到可执行文件：{args[0]}（是不是没装？）")
    if check and p.returncode != 0:
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        die(f"命令失败（退出码 {p.returncode}）：\n  {' '.join(args)}\n{out}")
    return p


def ask(prompt: str, default: str = "") -> str:
    tip = f"{prompt}（默认 {default}）: " if default else f"{prompt}: "
    try:
        v = input(tip).strip()
    except EOFError:
        v = ""
    v = (v or "").replace("\ufeff", "").strip()
    return v or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    v = ask(f"{prompt} [{d}]", "y" if default else "n").lower()
    return v.startswith("y")


# ────────────────────────────────────────────────── 配置与状态

def load_cfg(required: bool = True):
    r"""读配置：先看 %USERPROFILE%\.mcmesh\config.json，再看 exe 旁边的老位置（自动迁移）。"""
    path = None
    for cand in (CONFIG_PATH, TOOL_DIR / "config.json"):
        if cand.exists():
            path = cand
            break
    if path is None:
        if required:
            die("还没有配置过。先运行：python mcmesh.py init")
        return None
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    if path != CONFIG_PATH:                  # 老位置 → 迁到新位置
        try:
            CONFIG_PATH.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        except OSError:
            pass
    return cfg


def save_cfg(cfg) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    for cand in (STATE_PATH, TOOL_DIR / "state.json"):
        if cand.exists():
            try:
                st = json.loads(cand.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if cand != STATE_PATH:
                try:
                    STATE_PATH.write_text(cand.read_text(encoding="utf-8-sig"),
                                          encoding="utf-8")
                except OSError:
                    pass
            return st
    return {}


def save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def repo_dir(cfg) -> Path:
    if cfg.get("work_dir"):
        return Path(cfg["work_dir"])
    new = DATA_DIR / "repo"
    old = TOOL_DIR / "repo"                 # 老位置：exe 旁边
    if not new.exists() and old.exists():
        try:
            shutil.move(str(old), str(new))   # 自动搬家，数据不丢
        except Exception:
            return old
    return new


def instance_dir(cfg) -> Path:
    if not cfg.get("instance"):
        die("配置里没有 instance（整合包目录）。运行 python mcmesh.py init 重新配置")
    return Path(cfg["instance"])


def world_dir(cfg) -> Path:
    """真正被玩的存档目录。"""
    if cfg["mode"] == "server":
        return repo_dir(cfg) / "server" / "world"
    return instance_dir(cfg) / "saves" / cfg["world"]


def repo_world_dir(cfg) -> Path:
    """仓库里存档的镜像位置（mode=lan 时用）。"""
    return repo_dir(cfg) / "world"


def world_git_path(cfg) -> str:
    """存档在仓库里的相对路径（用来查"最近一次是谁上传的"）。"""
    return "server/world" if cfg["mode"] == "server" else "world"


# ────────────────────────────────────────────────── 镜像 / 指纹 / 备份

def mirror(src: Path, dst: Path, ignore=WORLD_IGNORE) -> int:
    """把 dst 变成和 src 完全一致（复制新增/改动，删掉多余的）。返回改动条目数。"""
    if not src.exists():
        die(f"源目录不存在：{src}")
    dst.mkdir(parents=True, exist_ok=True)
    wanted = set()
    changed = 0

    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ignore)
        rel = Path(root).relative_to(src)
        for name in files:
            if name in ignore:
                continue
            s = Path(root) / name
            d = dst / rel / name
            wanted.add((rel / name).as_posix())
            if d.exists():
                try:
                    ss, ds = s.stat(), d.stat()
                    if ss.st_size == ds.st_size and ss.st_mtime_ns == ds.st_mtime_ns:
                        continue
                except OSError:
                    pass
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            changed += 1

    for root, dirs, files in os.walk(dst, topdown=False):
        for name in files:
            if name in ignore:
                continue
            p = Path(root) / name
            if p.relative_to(dst).as_posix() not in wanted:
                try:
                    p.unlink()
                    changed += 1
                except OSError:
                    pass
        for name in dirs:
            p = Path(root) / name
            try:
                if not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass
    return changed


def fingerprint(path: Path, ignore=WORLD_IGNORE) -> str:
    """按 (相对路径, 大小, 修改时间) 算指纹，判断"本地是否有没上传的改动"。"""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in ignore)
        for name in sorted(files):
            if name in ignore:
                continue
            f = Path(root) / name
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f.relative_to(path).as_posix().encode("utf-8", "replace"))
            h.update(b"\0")
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
            h.update(b"\0")
    return h.hexdigest()


def dir_stats(path: Path) -> tuple[int, int]:
    n = total = 0
    if not path.exists():
        return 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
                n += 1
            except OSError:
                pass
    return n, total


def recently_written(path: Path, seconds: int = 45, ignore_before: float = 0.0) -> bool:
    """存档是不是刚刚还在被写（多半是游戏还开着）。

    ignore_before：早于这个时间戳的写入不算（用来忽略"工具自己刚写的那几份"，
    否则连着换两次角色，第二次会被自己上一次的写入拦住）。
    """
    if not path.exists():
        return False
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, (Path(root) / name).stat().st_mtime)
            except OSError:
                pass
    if not newest or newest <= ignore_before:
        return False
    return (time.time() - newest) < seconds


_JP_CACHE: dict = {"t": 0.0, "v": []}


def java_processes() -> list[str]:
    """正在跑的 Minecraft 进程（看进程比看文件时间靠谱：停在菜单时磁盘不写，但文件被占着）。

    结果缓存 5 秒：界面会定时轮询，别每次都去起一个 powershell。
    """
    if sys.platform != "win32":
        return []
    if time.time() - _JP_CACHE["t"] < 5:
        return list(_JP_CACHE["v"])
    try:
        quick = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq java.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=_CREATE_NO_WINDOW)
        if "java.exe" not in (quick.stdout or "").lower():
            quick2 = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq javaw.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8, creationflags=_CREATE_NO_WINDOW)
            if "javaw.exe" not in (quick2.stdout or "").lower():
                _JP_CACHE["t"] = time.time()
                _JP_CACHE["v"] = []
                return []
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='java.exe' or Name='javaw.exe'\" | "
             "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=12, creationflags=_CREATE_NO_WINDOW)
    except Exception:
        return []
    hits = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid, cmd = line.split("|", 1)
        low = cmd.lower()
        if any(k in low for k in ("minecraft", "fabric", "forge", "quilt")):
            hits.append(f"PID {pid}")
    _JP_CACHE["t"] = time.time()
    _JP_CACHE["v"] = hits
    return hits


def backup_world(path: Path, label: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{label}-{stamp}.zip"
    info(f"备份到 {target}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in WORLD_IGNORE]
            for name in files:
                if name in WORLD_IGNORE:
                    continue
                f = Path(root) / name
                z.write(f, f.relative_to(path).as_posix())
    prune_backups()
    return target


def prune_backups(keep: int = 12) -> None:
    files = sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


# ────────────────────────────────────────────────── Git

def git(cfg, *args, check=True):
    return run(["git", *args], cwd=repo_dir(cfg), check=check)


def git_out(cfg, *args) -> str:
    p = git(cfg, *args, check=False)
    return (p.stdout or "").strip()


def apply_config(updates: dict, make_repo: bool = True) -> dict:
    """把界面里填的一堆配置一次性写进去（不交互，给图形界面用）。

    顺序：存配置 → 建/更新本地仓库 → 记 git 身份 → 把人登记进名册 → 记同步指纹。
    """
    cfg = load_cfg(required=False) or dict(DEFAULT_CONFIG)
    for k, v in (updates or {}).items():
        if v is not None:
            cfg[k] = v
    save_cfg(cfg)
    if make_repo:
        rd = repo_dir(cfg)
        try:
            if not (rd / ".git").exists():
                if cfg.get("repo_url") and not (rd.exists() and any(rd.iterdir())):
                    rd.parent.mkdir(parents=True, exist_ok=True)
                    r = run(["git", "clone", cfg["repo_url"], str(rd)], check=False)
                    if r.returncode != 0:
                        warn("克隆失败（空仓库 / 没权限 / 地址不对），先建本地仓库")
                        init_local_repo(cfg)
                else:
                    init_local_repo(cfg)
            if (rd / ".git").exists():
                if cfg.get("repo_url") and not git_out(cfg, "remote", "get-url", "origin"):
                    run(["git", "remote", "add", "origin", cfg["repo_url"]], cwd=rd, check=False)
                seed_repo_files(cfg)
                ensure_git_identity(cfg)
                register_player(cfg, cfg.get("player", ""), "owner", "本机")
                if not git_out(cfg, "rev-parse", "--verify", "--quiet", "HEAD"):
                    git(cfg, "add", "-A")
                    git(cfg, "commit", "-m", "初始化：MCMesh 存档仓库")
        except SystemExit:
            raise
        except Exception as e:
            warn(f"仓库准备时出了点问题：{e}")
    st = load_state()
    st.setdefault("last_synced", fingerprint(world_dir(cfg)))
    st.setdefault("last_pull", "")
    st.setdefault("last_push", "")
    save_state(st)
    return cfg


def ensure_git_repo(cfg) -> None:
    rd = repo_dir(cfg)
    if not (rd / ".git").exists():
        die(f"仓库还没准备好：{rd}\n先运行 python mcmesh.py init")
    if not cfg.get("repo_url"):
        warn("配置里没有 repo_url，只能在本地用，推不上去")


def ensure_git_identity(cfg) -> None:
    """没配 git 用户名时补一个，不然 commit 会直接失败。"""
    rd = repo_dir(cfg)
    if not rd.exists():
        return
    if not git_out(cfg, "config", "user.name"):
        run(["git", "config", "user.name", cfg.get("player") or "player"], cwd=rd, check=False)
    if not git_out(cfg, "config", "user.email"):
        mail = (cfg.get("player") or "player").replace(" ", "") + "@mcmesh.local"
        run(["git", "config", "user.email", mail], cwd=rd, check=False)


def git_fetch(cfg, soft: bool = False) -> bool:
    """从远端取最新。返回 True=成功（或远端本来就空），False=软失败（断网/没权限）。"""
    branch = cfg["branch"]
    p = git(cfg, "fetch", "--prune", "origin", branch, check=False)
    if p.returncode == 0:
        return True
    err = ((p.stderr or "") + (p.stdout or "")).strip()
    low = err.lower()
    # 远端是空仓库 / 还没有这个分支：第一次上传前就是这状态，不算错误
    if "couldn't find remote ref" in low or "could not find remote ref" in low:
        return True
    if soft:
        warn("连不上远端（断网 / GitHub 被墙 / 没权限）—— 按本地已有的那份继续")
        return False
    die("无法从远端拉取，检查网络或权限：\n  " + err)


def _rev(cfg, ref: str) -> str:
    """解析一个 ref；不存在就返回空串（注意 rev-parse 不加 --verify 会把 ref 原样打印出来）。"""
    p = git(cfg, "rev-parse", "--verify", "--quiet", ref, check=False)
    return (p.stdout or "").strip() if p.returncode == 0 else ""


def remote_head(cfg) -> str:
    return _rev(cfg, f"origin/{cfg['branch']}")


def local_head(cfg) -> str:
    return _rev(cfg, "HEAD")


def is_ancestor(cfg, a: str, b: str) -> bool:
    if not a or not b:
        return False
    return git(cfg, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def repo_dirty(cfg) -> bool:
    return bool(git_out(cfg, "status", "--porcelain"))


def last_commit_info(cfg, path_filter: str = "") -> str:
    args = ["log", "-1", "--format=%an|%ad|%s", "--date=format:%Y-%m-%d %H:%M"]
    if path_filter:
        args += ["--", path_filter]
    line = git_out(cfg, *args)
    if not line:
        return "（还没有任何上传）"
    parts = line.split("|", 2)
    who = parts[0] if parts else ""
    when = parts[1] if len(parts) > 1 else ""
    what = parts[2] if len(parts) > 2 else ""
    return f"{who} @ {when}   {what}"


def commit_and_push(cfg, message: str, force: bool = False) -> bool:
    """提交当前工作区并推送。返回是否真的推上去了。"""
    ensure_git_repo(cfg)
    ensure_git_identity(cfg)
    git(cfg, "add", "-A")
    if not git_out(cfg, "diff", "--cached", "--name-only"):
        ok("存档没有变化，不用上传")
        return False

    offline = False
    if cfg.get("repo_url"):
        fetched = git_fetch(cfg, soft=True)
        offline = not fetched
        remote = "" if offline else remote_head(cfg)
        head = local_head(cfg)
        if offline:
            warn("连不上远端 —— 这次只提交到本地仓库，等网络好了再按 2 上一次")
        elif remote and head and not is_ancestor(cfg, remote, head):
            if not force:
                say()
                warn("远端有别人更新的存档，你这一版还没传上去：")
                info(f"远端最新：{last_commit_info(cfg, world_git_path(cfg))}")
                say()
                info("怎么选：")
                info("  · 我玩的是最新那版 -> python mcmesh.py pull   （先拉下来再玩）")
                info("  · 以我这一版为准   -> python mcmesh.py push --force")
                return False
            warn("按 --force 处理：丢弃远端那版，用你的存档覆盖")
            git(cfg, "reset", "--hard", f"origin/{cfg['branch']}")

    git(cfg, "commit", "-m", message)
    if not cfg.get("repo_url"):
        ok(f"已在本地提交：{message}")
        return False
    if offline:
        ok(f"已在本地提交：{message}（还没推到 GitHub）")
        return False

    p = git(cfg, "push", "origin", f"HEAD:{cfg['branch']}", check=False)
    if p.returncode != 0:
        err = ((p.stderr or "") + (p.stdout or "")).strip()
        warn("推送失败，但存档已经安全提交在本地仓库了。原因：")
        info(err.splitlines()[-1] if err else "未知")
        info("常见原因：仓库地址不对 / 没有写权限 / 没登录 GitHub")
        return False
    ok("已上传到远端仓库")
    return True


# ────────────────────────────────────────────────── 玩家身份

def offline_uuid(name: str) -> str:
    """和 Java 服务端一致：UUID.nameUUIDFromBytes("OfflinePlayer:" + name)。"""
    digest = hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


def players_path(cfg) -> Path:
    return repo_dir(cfg) / "players.json"


def load_players(cfg) -> dict:
    p = players_path(cfg)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            data.setdefault("players", {})
            return data
        except Exception:
            warn("players.json 损坏，将重建")
    return {"players": {}}


def save_players(cfg, data: dict) -> None:
    players_path(cfg).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def register_player(cfg, name: str, role: str = "member", note: str = "",
                    uid: str = "") -> str:
    """把玩家登记进 players.json（幂等），返回 UUID。

    uid 可以显式指定（正版账号的随机 UUID 没法由名字算出来）。
    """
    name = name.strip()
    if not name:
        die("玩家名不能为空")
    data = load_players(cfg)
    players = data["players"]
    uid = (uid or "").strip().lower() or offline_uuid(name)
    entry = players.get(name)
    if entry:
        if role and entry.get("role") != role:
            entry["role"] = role
        if note:
            entry["note"] = note
        entry["uuid"] = uid
    else:
        players[name] = {
            "uuid": uid,
            "role": role or "member",
            "note": note,
            "added": now_str(),
        }
    save_players(cfg, data)
    return uid


def write_server_identity_files(cfg) -> None:
    """根据 players.json 生成服务端认人的 whitelist.json / ops.json。"""
    server = repo_dir(cfg) / "server"
    server.mkdir(parents=True, exist_ok=True)
    data = load_players(cfg)
    whitelist, ops = [], []
    for name, e in sorted(data["players"].items()):
        uid = e.get("uuid") or offline_uuid(name)
        whitelist.append({"uuid": uid, "name": name})
        if str(e.get("role", "")).lower() in ("op", "admin", "owner"):
            ops.append({"uuid": uid, "name": name, "level": 4, "bypassesPlayerLimit": True})
    (server / "whitelist.json").write_text(json.dumps(whitelist, indent=2), encoding="utf-8")
    (server / "ops.json").write_text(json.dumps(ops, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────── NBT：解析 + 定点手术

def parse_nbt(raw: bytes) -> dict:
    """解析一段完整 NBT。int/long 数组也解出来（UUID 就存在这里）。"""
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        b = raw[pos: pos + n]
        pos += n
        return b

    def num(fmt: str, size: int):
        return struct.unpack(fmt, take(size))[0]

    def read_str() -> str:
        n = num(">H", 2)
        return take(n).decode("utf-8", "replace")

    def read_payload(tag: int):
        if tag == 1:
            return num(">b", 1)
        if tag == 2:
            return num(">h", 2)
        if tag == 3:
            return num(">i", 4)
        if tag == 4:
            return num(">q", 8)
        if tag == 5:
            return num(">f", 4)
        if tag == 6:
            return num(">d", 8)
        if tag == 7:
            n = num(">i", 4)
            return take(n)  # byte array，原样返回
        if tag == 8:
            return read_str()
        if tag == 9:
            sub = take(1)[0]
            n = num(">i", 4)
            return [read_payload(sub) for _ in range(max(0, n))]
        if tag == 10:
            out = {}
            while True:
                t = take(1)[0]
                if t == 0:
                    break
                key = read_str()
                out[key] = read_payload(t)
            return out
        if tag == 11:
            n = num(">i", 4)
            return list(struct.unpack(f">{n}i", take(n * 4)))
        if tag == 12:
            n = num(">i", 4)
            return list(struct.unpack(f">{n}q", take(n * 8)))
        return None

    try:
        if take(1)[0] != 10:
            return {}
        read_str()  # 根名字，一般空串
        return read_payload(10) or {}
    except Exception:
        return {}


def read_level_dat(path: Path) -> dict:
    try:
        return parse_nbt(gzip.open(path, "rb").read())
    except Exception:
        return {}


def nbt_skip(raw: bytes, pos: int, tag: int) -> int:
    """跳过 pos 处一个 tag 的 payload，返回下一个位置。"""
    if tag == 1:
        return pos + 1
    if tag == 2:
        return pos + 2
    if tag == 3:
        return pos + 4
    if tag == 4:
        return pos + 8
    if tag == 5:
        return pos + 4
    if tag == 6:
        return pos + 8
    if tag == 7:
        return pos + 4 + struct.unpack_from(">i", raw, pos)[0]
    if tag == 8:
        return pos + 2 + struct.unpack_from(">H", raw, pos)[0]
    if tag == 9:
        sub = raw[pos]
        n = struct.unpack_from(">i", raw, pos + 1)[0]
        p = pos + 5
        for _ in range(max(0, n)):
            p = nbt_skip(raw, p, sub)
        return p
    if tag == 10:
        p = pos
        while True:
            t = raw[p]
            if t == 0:
                return p + 1
            nl = struct.unpack_from(">H", raw, p + 1)[0]
            p = nbt_skip(raw, p + 3 + nl, t)
    if tag == 11:
        return pos + 4 + 4 * struct.unpack_from(">i", raw, pos)[0]
    if tag == 12:
        return pos + 4 + 8 * struct.unpack_from(">i", raw, pos)[0]
    raise ValueError(f"未知 NBT 类型 {tag}")


def nbt_entries(raw: bytes, start: int):
    """遍历 start 处的 compound，产出 (entry_start, name, tag, payload_start, payload_end)。"""
    p = start
    while True:
        t = raw[p]
        if t == 0:
            return
        nl = struct.unpack_from(">H", raw, p + 1)[0]
        name = raw[p + 3:p + 3 + nl].decode("utf-8", "replace")
        ps = p + 3 + nl
        pe = nbt_skip(raw, ps, t)
        yield p, name, t, ps, pe
        p = pe


def nbt_find(raw: bytes, path: tuple[str, ...]):
    """按路径找 compound 条目，返回最末层的 (entry_start, name, tag, payload_start, payload_end)。"""
    if not raw or raw[0] != 10:
        return None
    p = 1
    nl = struct.unpack_from(">H", raw, p)[0]
    cur = p + 2 + nl
    last = None
    for i, key in enumerate(path):
        found = None
        for entry in nbt_entries(raw, cur):
            if entry[1] == key:
                found = entry
                break
        if not found:
            return None
        last = found
        if i < len(path) - 1:
            if found[2] != 10:
                return None
            cur = found[3]
    return last


def nbt_wrap(payload: bytes) -> bytes:
    """把 compound payload 包成一份独立 NBT（tag10 + 空名字），也就是 .dat 玩家文件的格式。"""
    return b"\x0a\x00\x00" + payload


def nbt_unwrap(raw: bytes) -> bytes:
    if raw[0] != 10:
        raise ValueError("根部不是 compound")
    nl = struct.unpack_from(">H", raw, 1)[0]
    return raw[3 + nl:]


def read_player_dat(path: Path):
    """读 playerdata/<uuid>.dat（gzip 压缩的 NBT），返回角色 payload。"""
    try:
        return nbt_unwrap(gzip.open(path, "rb").read())
    except Exception:
        return None


def write_player_dat(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(nbt_wrap(payload))
    os.replace(tmp, path)


# ────────────────────────────────────────────────── 角色（房主身份）移植

def chars_dir(cfg) -> Path:
    d = repo_dir(cfg) / "chars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chars_index_path(cfg) -> Path:
    return chars_dir(cfg) / "index.json"


def load_chars_index(cfg) -> dict:
    p = chars_index_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {"owner": None, "players": {}}


def save_chars_index(cfg, data: dict) -> None:
    chars_index_path(cfg).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def char_file(cfg, name: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name).strip("_") or "player"
    return chars_dir(cfg) / f"{safe}.dat"


def get_host_char_payload(world: Path):
    """取出 level.dat -> Data.Player 的原始 NBT 字节；没有就返回 None。"""
    lvl = world / "level.dat"
    if not lvl.exists():
        return None
    try:
        raw = gzip.open(lvl, "rb").read()
    except Exception:
        return None
    entry = nbt_find(raw, ("Data", "Player"))
    return raw[entry[3]:entry[4]] if entry else None


def set_host_char_payload(world: Path, payload: bytes) -> str:
    """把 payload 写进 level.dat 的 Data.Player（没有就新建）。会先备份 + 写后校验。"""
    lvl = world / "level.dat"
    if not lvl.exists():
        die(f"找不到 {lvl}")
    raw = gzip.open(lvl, "rb").read()
    entry = nbt_find(raw, ("Data", "Player"))
    if entry:
        new = raw[:entry[3]] + payload + raw[entry[4]:]
        how = "替换"
    else:
        data_entry = nbt_find(raw, ("Data",))
        if not data_entry:
            die("level.dat 结构异常：找不到 Data 标签")
        end_pos = data_entry[4] - 1  # 指向 compound 的 END(0)
        block = b"\x0a" + struct.pack(">H", 6) + b"Player" + payload
        new = raw[:end_pos] + block + raw[end_pos:]
        how = "新建"

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / f"level.dat-{datetime.now():%Y%m%d-%H%M%S}.bak"
    shutil.copy2(lvl, bak)
    # level.dat 的备份只留最近 10 份，别把硬盘塞满
    olds = sorted(BACKUP_DIR.glob("level.dat-*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in olds[10:]:
        try:
            old.unlink()
        except OSError:
            pass
    tmp = lvl.with_name("level.dat.mcmesh-tmp")
    try:
        with gzip.open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, lvl)
    except OSError as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"写不进 level.dat（游戏可能还占着它）：{e}\n"
            "→ 请在 PCL 里完全关闭游戏（不是退回主菜单，是关掉进程）后再换角色") from e
    if get_host_char_payload(world) != payload:
        shutil.copy2(bak, lvl)
        die(f"写入后校验不一致，已自动回滚（备份：{bak}）")
    return how


def char_summary(payload: bytes) -> str:
    d = parse_nbt(nbt_wrap(payload))
    if not isinstance(d, dict) or not d:
        return "（解析不出来）"
    pos = d.get("Pos") or []
    inv = d.get("Inventory") or []
    parts = []
    if pos and len(pos) >= 3:
        parts.append(f"坐标 {pos[0]:.0f},{pos[1]:.0f},{pos[2]:.0f}")
    if d.get("XpLevel") is not None:
        parts.append(f"等级 {d.get('XpLevel')}")
    parts.append(f"背包 {len(inv) if isinstance(inv, list) else 0} 件")
    if d.get("Health") is not None:
        parts.append(f"血量 {d.get('Health'):.0f}")
    return " · ".join(parts)


def save_char(cfg, name: str, payload: bytes, set_owner: bool = True) -> Path:
    f = char_file(cfg, name)
    f.write_bytes(nbt_wrap(payload))
    idx = load_chars_index(cfg)
    idx.setdefault("players", {})[name] = {
        "file": f.name,
        "saved": now_str(),
        "summary": char_summary(payload),
    }
    if set_owner:
        idx["owner"] = name
    idx["updated"] = now_str()
    save_chars_index(cfg, idx)
    return f


def import_guest_chars(cfg, world: Path, skip: str = "") -> list[str]:
    """把 playerdata/ 里各位访客的角色也记进 chars/，这样他们下次当房主能接着用。

    skip 用来跳过当前房主自己：他的最新角色在 level.dat 里，
    不能被 playerdata/ 里那份（他当访客时的旧数据）覆盖回去。
    """
    pd = world / "playerdata"
    if not pd.is_dir():
        return []
    names = uuid_name_map(cfg)
    got = []
    for f in pd.glob("*.dat"):
        name = names.get(f.stem)
        if not name:
            continue
        if skip and name == skip:
            continue
        payload = read_player_dat(f)
        if payload is None:
            continue
        save_char(cfg, name, payload, set_owner=False)
        mark_char_world(cfg, name, world)
        # 顺带把这位访客的成就/统计也存进他的角色档案
        for sub, kind in (("stats", "stats"), ("advancements", "advancements")):
            src = world / sub / f"{f.stem}.json"
            if src.is_file():
                try:
                    shutil.copy2(src, char_aux_file(cfg, name, kind))
                except OSError:
                    pass
        got.append(name)
    return got


def mark_char_world(cfg, name: str, world) -> None:
    """记下这个角色在哪个存档里用过 —— 界面按存档过滤角色列表要用。"""
    if not name:
        return
    wn = world.name if isinstance(world, Path) else str(world)
    if not wn:
        return
    idx = load_chars_index(cfg)
    entry = (idx.get("players") or {}).get(name)
    if not entry:
        return
    worlds = [str(x) for x in (entry.get("worlds") or [])]
    if wn not in worlds:
        worlds.append(wn)
        entry["worlds"] = worlds
        idx["updated"] = now_str()
        save_chars_index(cfg, idx)


# ────────────────────────────────────────────────── 角色身上的东西（物品栏/装备）

_SLOT_LABEL = {100: "靴子", 101: "护腿", 102: "胸甲", 103: "头盔", -106: "副手", 150: "副手"}

_ITEM_CN = {
    "netherite_sword": "下界合金剑", "diamond_sword": "钻石剑", "iron_sword": "铁剑",
    "stone_sword": "石剑", "wooden_sword": "木剑", "golden_sword": "金剑",
    "bow": "弓", "crossbow": "弩", "shield": "盾牌", "arrow": "箭",
    "netherite_pickaxe": "下界合金镐", "diamond_pickaxe": "钻石镐", "iron_pickaxe": "铁镐",
    "stone_pickaxe": "石镐", "wooden_pickaxe": "木镐",
    "diamond_axe": "钻石斧", "iron_axe": "铁斧", "stone_axe": "石斧",
    "diamond_shovel": "钻石锹", "iron_shovel": "铁锹", "stone_shovel": "石锹",
    "netherite_helmet": "下界合金头盔", "diamond_helmet": "钻石头盔",
    "iron_helmet": "铁头盔", "golden_helmet": "金头盔", "chainmail_helmet": "锁链头盔",
    "leather_helmet": "皮革帽子",
    "netherite_chestplate": "下界合金胸甲", "diamond_chestplate": "钻石胸甲",
    "iron_chestplate": "铁胸甲", "golden_chestplate": "金胸甲", "leather_chestplate": "皮革外套",
    "netherite_leggings": "下界合金护腿", "diamond_leggings": "钻石护腿",
    "iron_leggings": "铁护腿", "golden_leggings": "金护腿", "leather_leggings": "皮革裤子",
    "netherite_boots": "下界合金靴子", "diamond_boots": "钻石靴子",
    "iron_boots": "铁靴子", "golden_boots": "金靴子", "leather_boots": "皮革靴子",
    "elytra": "鞘翅", "totem_of_undying": "不死图腾", "ender_pearl": "末影珍珠",
    "cooked_beef": "牛排", "bread": "面包", "golden_apple": "金苹果",
    "diamond": "钻石", "emerald": "绿宝石", "iron_ingot": "铁锭",
    "cobblestone": "圆石", "oak_planks": "橡木木板", "torch": "火把",
    "water_bucket": "水桶", "lava_bucket": "岩浆桶", "bucket": "桶",
}


def pretty_item(item_id: str) -> str:
    """把 minecraft:diamond_sword 变成"钻石剑"；没收录的就去掉命名空间、下划线换空格。"""
    if not isinstance(item_id, str) or not item_id:
        return "?"
    short = item_id.split(":")[-1]
    if short in _ITEM_CN:
        return _ITEM_CN[short]
    return short.replace("_", " ")


def _item_entry(entry) -> dict:
    """统一成 {id, count, ench}，兼容 1.20.5 前后两种物品 NBT 格式。"""
    if not isinstance(entry, dict):
        return {}
    iid = entry.get("id")
    if not isinstance(iid, str):
        iid = str(iid) if iid is not None else ""
    cnt = entry.get("count", entry.get("Count", 1))
    if not isinstance(cnt, int):
        try:
            cnt = int(cnt)
        except Exception:
            cnt = 1
    ench = 0
    comps = entry.get("components")
    if isinstance(comps, dict):
        for key in ("minecraft:enchantments", "minecraft:stored_enchantments"):
            v = comps.get(key)
            if isinstance(v, dict):
                lv = v.get("levels") or v.get("")
                if isinstance(lv, dict):
                    ench = len(lv)
    tag = entry.get("tag")
    if not ench and isinstance(tag, dict):
        el = tag.get("Enchantments")
        if isinstance(el, list):
            ench = len(el)
    return {"id": iid, "count": cnt, "ench": ench}


def char_inventory(payload: bytes) -> dict:
    """解析一个角色身上的东西：装备、副手、快捷栏、背包。"""
    d = parse_nbt(nbt_wrap(payload))
    inv = d.get("Inventory") if isinstance(d, dict) else None
    res = {"equip": {}, "hotbar": [], "main": [], "other": [], "hint": d.get("SelectedItemSlot") if isinstance(d, dict) else None}
    if not isinstance(inv, list):
        return res
    for entry in inv:
        it = _item_entry(entry)
        if not it.get("id"):
            continue
        slot = entry.get("Slot", entry.get("slot", 0))
        if not isinstance(slot, int):
            try:
                slot = int(slot)
            except Exception:
                slot = 0
        if slot in _SLOT_LABEL:
            res["equip"][_SLOT_LABEL[slot]] = it
        elif 0 <= slot <= 8:
            res["hotbar"].append((slot, it))
        elif 9 <= slot <= 35:
            res["main"].append((slot, it))
        else:
            res["other"].append((slot, it))
    # 新版把装备单独放 equipment 里
    eq = d.get("equipment") if isinstance(d, dict) else None
    if isinstance(eq, dict):
        for k, label in (("head", "头盔"), ("chest", "胸甲"), ("legs", "护腿"),
                         ("feet", "靴子"), ("offhand", "副手"), ("mainhand", "主手")):
            it = _item_entry(eq.get(k))
            if it.get("id"):
                res["equip"][label] = it
    for k in ("hotbar", "main"):
        res[k].sort(key=lambda x: x[0])
    return res


def inventory_lines(payload: bytes) -> list[str]:
    """给界面用的纯文本行。"""
    inv = char_inventory(payload)
    lines: list[str] = []
    eq = inv["equip"]
    if eq:
        parts = []
        for label in ("头盔", "胸甲", "护腿", "靴子", "副手", "主手"):
            if label in eq:
                it = eq[label]
                s = pretty_item(it["id"])
                if it["count"] > 1:
                    s += f" x{it['count']}"
                if it["ench"]:
                    s += f"（附魔×{it['ench']}）"
                parts.append(f"{label}: {s}")
        if parts:
            lines += ["【装备】"] + [f"  {p}" for p in parts]

    def dump(title, items):
        if not items:
            return
        lines.append(f"【{title}】{len(items)} 格")
        for slot, it in items:
            s = pretty_item(it["id"])
            if it["count"] > 1:
                s += f" x{it['count']}"
            if it["ench"]:
                s += f"（附魔×{it['ench']}）"
            hold = "  ← 手持" if title.startswith("快捷栏") and inv.get("hint") == slot else ""
            lines.append(f"  {slot:>2}  {s}{hold}")

    dump("快捷栏", inv["hotbar"])
    dump("背包", inv["main"])
    dump("其它", inv["other"])
    if not lines:
        lines = ["（身上什么都没有）"]
    return lines


def load_char(cfg, name: str):
    f = char_file(cfg, name)
    if not f.exists():
        return None
    try:
        return nbt_unwrap(f.read_bytes())
    except Exception:
        return None


# ────────────────────────────────────────────────── 角色档案：身体 + 成就/统计

def char_aux_dir(cfg) -> Path:
    d = chars_dir(cfg) / "aux"
    d.mkdir(parents=True, exist_ok=True)
    return d


def char_aux_file(cfg, name: str, kind: str) -> Path:
    return char_aux_dir(cfg) / f"{char_file(cfg, name).stem}.{kind}.json"


def detect_local_uuid(world: Path, remember: bool = True) -> str:
    """本机玩家在存档里的 UUID（房主那份 playerdata 的目录名）。"""
    st = load_state()
    if st.get("local_uuid"):
        return st["local_uuid"]
    host = get_host_char_payload(world)
    if host is None:
        return ""
    pd = world / "playerdata"
    if pd.is_dir():
        for f in pd.glob("*.dat"):
            if read_player_dat(f) == host:
                if remember:
                    st["local_uuid"] = f.stem.lower()
                    save_state(st)
                return f.stem.lower()
    return ""


def other_players_uuids(cfg, me: str = "") -> set[str]:
    """别人名下的 URL / 别名集合 —— 这些 playerdata 文件绝对不能碰。

    踩过的坑：换角色时"候选 UUID"是靠"谁的文件内容和当前身体一样"推出来的，
    如果刚换走的那具身体正好是别人当房主时留下的（他的访客文件也存了同一份），
    这个人的 UUID 就会混进候选里，接着被写入**你的**身体 → 他的进度被覆盖。
    """
    me = me or cfg.get("player", "")
    out: set[str] = set()
    try:
        data = load_players(cfg)
    except Exception:
        return out
    for name, v in (data.get("players") or {}).items():
        if not isinstance(v, dict) or name == me:
            continue
        u = str(v.get("uuid", "")).lower()
        if u:
            out.add(u)
        for a in (v.get("aliases") or []):
            a = str(a).lower()
            # 00000000-… 那种客户端 UUID 往往是"这台机器"的，不算某个人的专属身份
            if a and not a.startswith("00000000-"):
                out.add(a)
    return out


def remember_client_uuids(cfg, world: Path, extra=()) -> list[str]:
    """记住这台机器上用过的"客户端 UUID"（00000000-… 那种）。

    为什么需要记：换账号 / 游戏重建身份时会冒出一个**全新的** UUID，
    新拉下来的存档里又没有这些文件 —— 不记下来，就没法提前把成就写进去。
    """
    st = load_state()
    keep = [str(u).lower() for u in (st.get("client_uuids") or [])]
    for u in list(extra) + [body_uuid(get_host_char_payload(world))]:
        u = (u or "").lower()
        if u.startswith("00000000-") and u not in keep:
            keep.append(u)
    for sub in ("playerdata", "stats", "advancements"):
        d = world / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            stem = (f.stem if f.suffix else f.name).lower()
            if stem.startswith("00000000-") and stem not in keep:
                keep.append(stem)
    keep = keep[-8:]
    if keep != (st.get("client_uuids") or []):
        st["client_uuids"] = keep
        save_state(st)
    return keep


def plausible_local_uuids(cfg, world: Path) -> list[str]:
    """本机玩家可能用的所有 UUID —— 排在最前面的是"游戏真的在用"的那个。

    单人世界里"本地玩家"的 UUID 会变：客户端 UUID（00000000-0000-300a-… / …3004-…）、
    名字算出的 UUID、还有身体里自带的 UUID 字段。谁在生效**只能靠证据**：
      ① level.dat 里 Player 自带的 UUID 字段（游戏自己记的）
      ② 存档里最近被游戏写过的那份 stats/advancements（mtime 最新）
      ③ 历史记录 state.local_uuid
      ④ 名字算出的 UUID（当访客时用）
      ⑤ 世界目录里所有 00000000-… 形状的客户端 UUID 文件
    别人的 UUID 一律排除（见 other_players_uuids）。
    """
    blocked = other_players_uuids(cfg)
    out: list[str] = []

    def add(u: str) -> None:
        u = (u or "").lower()
        if u and u not in out and u not in blocked:
            out.append(u)

    # ① 身体里自带的 UUID（最硬的证据：游戏存档时写进去的）
    host = get_host_char_payload(world)
    if host is not None:
        add(body_uuid(host))
    # ② 最近被写过的那份成就/统计
    add(newest_aux_owner(world))
    # ③④ 历史记录 + 名字算出来的访客身份
    add(load_state().get("local_uuid"))
    add(offline_uuid(cfg.get("player", "")))
    # ⑤ 世界目录里所有客户端形状的 UUID + 历史上记下来的（新拉存档时也能覆盖到）
    for sub in ("playerdata", "stats", "advancements"):
        d = world / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            stem = f.stem.lower() if f.suffix else f.name.lower()
            if stem.startswith("00000000-"):
                add(stem)
    for u in remember_client_uuids(cfg, world):
        add(u)
    # 最后：内容上和当前身体一样的 playerdata 文件
    pd = world / "playerdata"
    if host is not None and pd.is_dir():
        for f in pd.glob("*.dat"):
            if read_player_dat(f) == host:
                add(f.stem)
    return out


def body_uuid(payload: bytes) -> str:
    """玩家 NBT 里自带的 UUID 字段（int[4] 或 long[2]）→ 标准 UUID 字符串。"""
    if not payload:
        return ""
    try:
        d = parse_nbt(nbt_wrap(payload))
    except Exception:
        return ""
    if not isinstance(d, dict):
        return ""
    raw = d.get("UUID")
    try:
        if isinstance(raw, list) and len(raw) == 4:
            b = b"".join(int(x).to_bytes(4, "big", signed=True) for x in raw)
        elif isinstance(raw, list) and len(raw) == 2:
            b = (int(raw[0]).to_bytes(8, "big", signed=True)
                 + int(raw[1]).to_bytes(8, "big", signed=True))
        else:
            return ""
        u = str(uuid.UUID(bytes=b))
    except Exception:
        return ""
    return "" if set(u.replace("-", "")) <= {"0"} else u


def newest_aux_owner(world: Path) -> str:
    """存档里 stats/advancements 最近被写过的是哪个 UUID（＝游戏现在在用的那个）。"""
    best, newest = "", 0.0
    for sub in ("stats", "advancements"):
        d = world / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m > newest:
                newest, best = m, f.stem.lower()
    return best


def live_player_uuid(cfg, world: Path, remember: bool = True) -> str:
    """游戏此刻真正在用的本机玩家 UUID —— 成就/统计必须写这个，否则白写。"""
    cands = plausible_local_uuids(cfg, world)
    if not cands:
        return ""
    live = cands[0]
    if remember:
        st = load_state()
        if st.get("local_uuid") != live:
            st["local_uuid"] = live
            save_state(st)
    return live


def best_local_uuid(cfg, world: Path) -> str:
    """在候选里挑"最近被游戏写过"的那个 —— 成就/统计要跟着它走。"""
    best, newest = "", 0.0
    for u in plausible_local_uuids(cfg, world):
        for sub in ("advancements", "stats"):
            p = world / sub / f"{u}.json"
            if p.is_file():
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest, best = m, u
    if best:
        st = load_state()
        if st.get("local_uuid") != best:
            st["local_uuid"] = best
            save_state(st)
        return best
    return detect_local_uuid(world)


def save_char_aux(cfg, name: str, world: Path) -> list[str]:
    """把 统计/成就 存进这个角色的档案 —— 直接把所有身份文件合并成并集写进档案。

    这样"游戏这次把成就写到哪个 UUID 了"都不影响结果：全都合并进来。
    """
    if not name:
        return []
    r = bind_aux(cfg, name, world)
    got = []
    if r.get("advancements", -1) >= 0:
        got.append("advancements")
    if r.get("stats", -1) >= 0:
        got.append("stats")
    return got


def _aux_done(path: Path) -> int:
    """数一份 advancements/stats JSON 里"真正拿到手"的条目数（拿不到就返回 -1）。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return -1
    return _aux_done_dict(d)


def _aux_done_dict(d) -> int:
    if not isinstance(d, dict):
        return -1
    return sum(1 for v in d.values() if isinstance(v, dict) and v.get("done"))


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def merge_advancements(dicts) -> dict:
    """把几份 advancements 合并成一份"并集"：任何一份里解锁过的，就算解锁过。"""
    out: dict = {}
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for key, val in d.items():
            if not isinstance(val, dict):
                continue
            tgt = out.setdefault(key, {"criteria": {}, "done": False})
            crit = val.get("criteria")
            if isinstance(crit, dict):
                for c, done in crit.items():
                    if done:
                        tgt["criteria"][c] = True
                    else:
                        tgt["criteria"].setdefault(c, False)
            if val.get("done"):
                tgt["done"] = True
    return out


def merge_stats(dicts) -> dict:
    """统计合并：同一个字段取最大值（不是相加，避免重复快照被算两次）。"""
    def merge(a: dict, b: dict) -> dict:
        for k, v in b.items():
            if isinstance(v, dict):
                a[k] = merge(a.get(k) or {}, v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                old = a.get(k)
                if not isinstance(old, (int, float)) or v > old:
                    a[k] = v
            else:
                a.setdefault(k, v)
        return a

    out: dict = {}
    for d in dicts:
        if isinstance(d, dict):
            out = merge(out, d)
    return out


def _is_locals_character(cfg, name: str, world: Path) -> bool:
    """这个角色是不是"这台机器现在正在用的那具身体"？

    只有这种时候，"本机的客户端 UUID 文件"才算他的数据。
    否则（比如在给朋友的角色建档案）绝不能把本机玩家的成就混进去 —— 踩过这个坑。
    """
    if not name:
        return False
    if name == cfg.get("player", ""):
        return True
    host = get_host_char_payload(world)
    if host is None:
        return False
    fp = body_fingerprint(host)
    if not fp:
        return False
    f = char_file(cfg, name)
    if not f.exists():
        return False
    try:
        return body_fingerprint(nbt_unwrap(f.read_bytes())) == fp
    except Exception:
        return False


def aux_identities(cfg, name: str, world: Path) -> list[str]:
    """这个角色可能挂在哪些 UUID 名下。

    · 他自己的名字算出的 UUID + 名册里的别名 —— 永远算
    · 本机的客户端 UUID（00000000-…）—— **只有**在他就是"当前这具身体"时才算
    """
    ids: list[str] = []

    def add(u: str) -> None:
        u = (u or "").lower()
        if u and u not in ids:
            ids.append(u)

    add(offline_uuid(name) if name else "")
    entry = (load_players(cfg).get("players") or {}).get(name) or {}
    for a in (entry.get("aliases") or []):
        add(str(a))
    if _is_locals_character(cfg, name, world):
        for u in plausible_local_uuids(cfg, world):
            add(u)
    return ids


def collect_aux(cfg, name: str, world: Path, kind: str) -> list[dict]:
    """把这个角色所有能找到的 统计/成就 JSON 都读出来（档案 + 各 UUID 的）。"""
    out = []
    paths = [char_aux_file(cfg, name, kind)]
    paths += [world / kind / f"{u}.json" for u in aux_identities(cfg, name, world)]
    for p in paths:
        if p.is_file():
            d = _read_json(p)
            if isinstance(d, dict):
                out.append(d)
    return out


def bind_aux(cfg, name: str, world: Path) -> dict:
    """把角色的 统计/成就 合并成一份，写回**所有**身份文件 + 档案。

    这就是"把角色所有相关数据绑到身上"：不管游戏这次读哪个 UUID，都是同一份（并集）。
    返回 {"advancements": 条数, "stats": 条数, "uuids": [...]}。
    """
    ids = aux_identities(cfg, name, world)
    own_guest = offline_uuid(cfg.get("player", ""))
    if name and name != cfg.get("player", "") and own_guest in ids and own_guest != offline_uuid(name):
        ids = [u for u in ids if u != own_guest]     # 别污染"我自己当访客"的那份
    res = {"advancements": -1, "stats": -1, "uuids": ids}
    if not ids:
        return res
    for sub, kind in (("advancements", "advancements"), ("stats", "stats")):
        srcs = collect_aux(cfg, name, world, kind)
        if not srcs:
            continue
        merged = (merge_advancements(srcs) if kind == "advancements" else merge_stats(srcs))
        if not merged:
            continue
        blob = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
        try:
            char_aux_file(cfg, name, kind).parent.mkdir(parents=True, exist_ok=True)
            char_aux_file(cfg, name, kind).write_text(blob, encoding="utf-8")
        except OSError:
            pass
        for uid in ids:
            try:
                (world / sub).mkdir(exist_ok=True)
                (world / sub / f"{uid}.json").write_text(blob, encoding="utf-8")
            except OSError:
                pass
        res[kind] = _aux_done_dict(merged) if kind == "advancements" else len(merged)
    return res


def best_aux_source(cfg, name: str, world: Path, kind: str):
    """这个角色的 统计/成就，哪一份最全？（保留给老调用方 / 展示用）"""
    cands: list[Path] = []
    a = char_aux_file(cfg, name, kind)
    if a.is_file():
        cands.append(a)
    for u in aux_identities(cfg, name, world):
        p = world / kind / f"{u}.json"
        if p.is_file():
            cands.append(p)
    if not cands:
        return None
    return max(cands, key=lambda p: (_aux_done(p), p.stat().st_size))


def apply_char_aux(cfg, name: str, world: Path) -> list[str]:
    """把角色档案里的 统计/成就 写回本机玩家 —— 合并成一份并集，写到所有身份文件。

    · 源：档案 + 所有 UUID 的现有文件，**取并集**（不会越切越少）
    · 目标：本机所有候选 UUID + 他自己名字算出的 UUID + 档案
    · 例外：换的是**别人的**角色时，不写"我自己当访客"的那份（名字算出的 UUID）
    """
    if not name:
        return []
    r = bind_aux(cfg, name, world)
    got = []
    if r.get("advancements", -1) >= 0:
        got.append("advancements")
    if r.get("stats", -1) >= 0:
        got.append("stats")
    return got


def body_fingerprint(payload: bytes) -> str:
    """"这具身体是谁"的稳定指纹：等级 + 背包/末影箱内容。

    故意不含坐标、朝向、视角、饥饿值、经验总值——那些每隔几分钟就变，
    含进去就永远匹配不上（以前的教训：换角色后身体一动就认不出人了）。
    """
    if not payload:
        return ""
    try:
        d = parse_nbt(nbt_wrap(payload))
    except Exception:
        return ""
    if not isinstance(d, dict):
        return ""

    def norm(items):
        out = []
        if isinstance(items, list):
            for e in items:
                if isinstance(e, dict) and e.get("id"):
                    out.append((e.get("Slot"), e.get("id"), e.get("count", 1)))
        return sorted(out, key=lambda x: (str(x[0]), str(x[1]), str(x[2])))

    key = (d.get("XpLevel"), norm(d.get("Inventory")), norm(d.get("EnderItems")))
    return hashlib.sha1(repr(key).encode()).hexdigest()


def identify_payload_owner(cfg, payload: bytes) -> str:
    """这份身体是谁的？按"稳定指纹"认，不按坐标认。

    顺序：
      ① chars/ 里某个角色的身体指纹一致，而且只有一个 → 就是他
      ② playerdata/ 里某个文件的指纹一致，且那个 UUID 在名册里有名字
      ③ 都认不出 → 返回空串（调用方回退到记录里的 host_char_owner）
    """
    fp = body_fingerprint(payload)
    if not fp:
        return ""
    matches = []
    for p in chars_dir(cfg).glob("*.dat"):
        try:
            if body_fingerprint(nbt_unwrap(p.read_bytes())) == fp:
                matches.append(p.stem)
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        owner = load_state().get("host_char_owner") or ""
        return owner if owner in matches else ""

    world = world_dir(cfg)
    pd = world / "playerdata"
    if pd.is_dir():
        names = uuid_name_map(cfg)
        hits = set()
        for f in pd.glob("*.dat"):
            try:
                if body_fingerprint(read_player_dat(f)) == fp:
                    nm = names.get(f.stem.lower(), "")
                    if nm:
                        hits.add(nm)
            except Exception:
                continue
        if len(hits) == 1:
            return hits.pop()
    return ""


def mirror_body_to_guest(cfg, world: Path, name: str, payload: bytes) -> list[str]:
    r"""同一个人有两个身份档案：房主用客户端 UUID，访客用"名字算出的 UUID"。

    这里把"访客"那份也写成同一具身体，否则换房主后他当访客进来会看到老数据/空档。
    """
    uid = offline_uuid(name)
    notes: list[str] = []
    pd = world / "playerdata"
    try:
        pd.mkdir(exist_ok=True)
    except OSError:
        return notes

    dst = pd / f"{uid}.dat"
    try:
        same = dst.exists() and read_player_dat(dst) == payload
        if same:
            notes.append(f"访客身份（{uid[:8]}…）本来就是这具身体")
        else:
            if dst.exists():
                bak = char_aux_dir(cfg) / f"{char_file(cfg, name).stem}.guest-backup.dat"
                shutil.copy2(dst, bak)
                notes.append(f"访客身份（{uid[:8]}…）也同步了（旧的那份备份成 {bak.name}）")
            else:
                notes.append(f"访客身份（{uid[:8]}…）已写入，加入别人世界也是这具身体")
            write_player_dat(dst, payload)
    except Exception as e:
        notes.append(f"访客身份同步失败：{e}")

    # 统计/成就也同样对齐（房主那份 → 访客那份）
    local = detect_local_uuid(world)
    if local and local != uid:
        for sub in ("stats", "advancements"):
            src = world / sub / f"{local}.json"
            if src.is_file():
                try:
                    (world / sub).mkdir(exist_ok=True)
                    shutil.copy2(src, world / sub / f"{uid}.json")
                except OSError:
                    pass
    return notes


def sync_local_identity_body(cfg, world: Path, payload: bytes,
                             uuids: list[str] = None, name: str = "") -> list[str]:
    r"""把换进来的身体写进"本机玩家"可能用的所有 playerdata 文件。

    单人世界里本机玩家的 UUID 可能有多个（客户端 UUID、名字算出的 UUID、
    游戏自己生成的 00000000-…），游戏到底读哪个不确定，所以全都写一遍。
    

    游戏读房主进度时看的是 playerdata\<本机客户端 UUID>.dat 和 level.dat 两边，
    两边不一致就可能被判成"没有这个玩家"→ 造一个全新的 → 看起来进度清零。
    """
    st = load_state()
    # 只写"房主身份"：本机客户端 UUID（含历史上出现过的那些）。
    # 别人的访客身份文件不能碰 —— 那是他们自己的进度。
    host_ids = [st.get("local_uuid"), detect_local_uuid(world)]
    blocked = other_players_uuids(cfg, name if name else cfg.get("player", ""))
    ids: list[str] = []
    for u in list(uuids or []) + host_ids:
        u = (u or "").lower()
        if u and u not in ids and u not in blocked:
            ids.append(u)
    # 只有当换的就是本机玩家自己的角色时，才同步他自己的访客身份
    own_guest = offline_uuid(cfg.get("player", ""))
    if name and name == cfg.get("player"):
        if own_guest and own_guest not in ids:
            ids.append(own_guest)
    elif own_guest:
        # 换的是别人的角色：别把我"当访客"的那份档案也写成他的身体
        ids = [u for u in ids if u != own_guest]
    if not ids:
        return []
    pd = world / "playerdata"
    try:
        pd.mkdir(exist_ok=True)
    except OSError:
        return []
    done, notes = [], []
    for uid in ids:
        dst = pd / f"{uid}.dat"
        try:
            if dst.exists() and read_player_dat(dst) == payload:
                continue
            if dst.exists():
                bak = char_aux_dir(cfg) / f"local-{uid[:8]}.backup.dat"
                shutil.copy2(dst, bak)
            write_player_dat(dst, payload)
            done.append(uid[:8])
        except Exception as e:
            notes.append(f"   {uid[:8]}… 写入失败：{e}")
    if done:
        notes.insert(0, f"身体已写入本机身份：{'、'.join(done)}…（共 {len(done)} 份）")
    else:
        notes.insert(0, "本机身份的文件本来就是这具身体")
    return notes


def stash_current_host_char(cfg, world: Path = None, quiet: bool = False) -> str:
    """把 level.dat 里现在装着的角色先存回 chars/（换角色 / 上传前必须先做，否则会丢进度）。"""
    wd = world or world_dir(cfg)
    payload = get_host_char_payload(wd)
    if payload is None:
        return ""
    st = load_state()
    owner = identify_payload_owner(cfg, payload) or st.get("host_char_owner") or "房主角色"
    # 注意：绝不拿"这台机器现在的账号名"来给存档里的身体起名 ——
    # 存档里的房主角色是"跟着存档走"的，谁启动游戏谁就在用这具身体，
    # 用账号名去命名会凭空多出一个同名同身的假角色（踩过这个坑）。
    if owner == "房主角色":
        info("这具身体还认不出是谁的，先记成「房主角色」（chars --as 你的名字 可以改名）")
    save_char(cfg, owner, payload)
    aux = save_char_aux(cfg, owner, wd)
    st["host_char_owner"] = owner
    save_state(st)
    mark_char_world(cfg, owner, wd)
    # 顺带把"这台机器客户端的 UUID"登记成当前玩家的别名，界面才能显示名字而不是一串 UUID
    local_uid = detect_local_uuid(wd, remember=False)
    if local_uid:
        add_alias(cfg, cfg["player"], local_uid, "本机客户端UUID")
    if not quiet:
        tail = f"（含 {'/'.join(aux)}）" if aux else ""
        info(f"先把当前角色存回 chars/{owner}.dat{tail}")
        for n in mirror_body_to_guest(cfg, wd, owner, payload):
            info(f"     {n}")
    else:
        mirror_body_to_guest(cfg, wd, owner, payload)
    return owner


# ────────────────────────────────────────────────── U 盘 / 任意文件夹 搬运

def list_drives() -> list[tuple[str, str]]:
    """列出盘符，U 盘排前面。"""
    out: list[tuple[str, str]] = []
    if sys.platform != "win32":
        return out
    types = {2: "★ 可移动（U 盘）", 3: "本地磁盘", 4: "网络位置", 5: "光驱", 6: "内存盘"}
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask >> i & 1:
                root = f"{chr(65 + i)}:\\"
                t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
                out.append((root, types.get(t, f"类型 {t}")))
    except Exception:
        pass
    out.sort(key=lambda x: (0 if "可移动" in x[1] else 1, x[0]))
    return out


def _normalize_dir(text: str) -> Path:
    """把输入整成绝对路径；顺手认「桌面 / Desktop」这种说法。"""
    t = (text or "").strip().strip('"').strip("'").strip()
    if not t:
        return Path(".")
    low = t.lower().rstrip("\\/")
    if low in ("desktop", "桌面", "桌面文件夹", "desk"):
        return Path.home() / "Desktop"
    p = Path(t).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p  # 相对路径按"当前目录"算，然后统一转成绝对路径
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def resolve_dir(cfg, given, key: str, title: str, create: bool = True) -> Path:
    """决定用哪个文件夹：命令行给了就用；否则用上次的；再否则让用户从盘符里挑。"""
    p = None
    if given is not None and str(given).strip():
        p = _normalize_dir(given)
    else:
        saved = (cfg.get(key) or "").strip()
        if saved and _normalize_dir(saved).parent.exists():
            last = _normalize_dir(saved)
            info(f"上次用的目录：{last}")
            if not ask_yes("   换成别的目录？", False):
                p = last
        if p is None:
            drives = list_drives()
            if drives:
                say("可用的盘：")
                for i, (root, t) in enumerate(drives, 1):
                    say(f"   {i}) {root:<4} {t}")
                sel = ask(f"   {title}（填编号 / 输「桌面」/ 粘贴完整路径）", "1")
            else:
                sel = ask(f"   {title}（完整路径）", "")
            sel = (sel or "").strip()
            if sel.isdigit() and drives and 1 <= int(sel) <= len(drives):
                p = Path(drives[int(sel) - 1][0]) / "MCMesh存档"
            elif sel:
                p = _normalize_dir(sel)
    if p is None:
        die("没有选择目录，已取消")
    if create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(f"这个目录建不起来：{p}\n  {e}")
    elif not p.is_dir():
        die(f"目录不存在：{p}")
    cfg[key] = str(p)
    save_cfg(cfg)
    ok(f"目录：{p}")
    return p


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text).strip("_") or "world"


def export_zip(cfg, dest: Path) -> Path:
    """把当前存档（连同角色记录）打成一个 zip 放到 dest。"""
    wd = world_dir(cfg)
    if not wd.exists() or not any(wd.iterdir()):
        die(f"本地还没有存档：{wd}")
    if recently_written(wd):
        warn("存档 45 秒内还在被写入 —— 如果游戏还开着，导出的会是没写完的存档")
        info("建议先退出游戏再导出")
    n, size = dir_stats(wd)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"mc-{_safe_name(cfg['world'])}-{_safe_name(cfg['player'])}-{stamp}.zip"
    target = dest / name
    info(f"打包 {n} 个文件（{human_size(size)}）…")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for root, dirs, files in os.walk(wd):
            dirs[:] = [d for d in dirs if d not in WORLD_IGNORE]
            for f in files:
                if f in WORLD_IGNORE:
                    continue
                fp = Path(root) / f
                z.write(fp, fp.relative_to(wd).as_posix())
        for f in sorted(chars_dir(cfg).glob("*.dat")):
            z.write(f, f"_mcmesh_chars/{f.name}")

    manifest = json.dumps({
        "player": cfg["player"],
        "world": cfg["world"],
        "time": now_str(),
        "mc": cfg.get("mc_version", ""),
        "files": n,
        "bytes": size,
        "summary": world_summary(cfg),
    }, ensure_ascii=False, indent=2)
    (dest / f"{target.stem}.json").write_text(manifest, encoding="utf-8")

    # 再留一份 latest.zip / latest.json，对方一键导入时就用它
    latest = dest / "latest.zip"
    try:
        if latest.exists():
            latest.unlink()
        shutil.copy2(target, latest)
        (dest / "latest.json").write_text(manifest, encoding="utf-8")
    except OSError as e:
        warn(f"复制 latest.zip 失败（不影响上面那份 zip）：{e}")

    # 只清理自己产生的旧包，不动别人的文件
    olds = sorted(dest.glob("mc-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in olds[10:]:
        try:
            old.unlink()
        except OSError:
            pass

    ok(f"已导出：{target}")
    info(f"体积 {human_size(target.stat().st_size)}（压缩后）")
    return target


def import_zip(cfg, src: Path, force: bool = False) -> None:
    """从 src 里挑最新的存档包，备份本地后覆盖回来。"""
    wd = world_dir(cfg)
    picks = []
    latest = src / "latest.zip"
    if latest.exists():
        picks.append(latest)
    picks += sorted(src.glob("mc-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)

    chosen = None
    for cand in picks:
        try:
            with zipfile.ZipFile(cand) as zf:
                if "level.dat" in zf.namelist():
                    chosen = cand
                    break
        except Exception:
            continue
    if chosen is None:
        listing = [p.name for p in sorted(src.glob("*"))][:8]
        die(f"{src} 里没找到可用的存档包（要含 level.dat 的 zip）\n  目录里有：{listing}")

    info(f"读取：{chosen.name}")
    man = {}
    for cand in (chosen.with_suffix(".json"), Path(str(chosen) + ".json")):
        if cand.exists():
            try:
                man = json.loads(cand.read_text(encoding="utf-8-sig"))
                break
            except Exception:
                man = {}
    if man:
        info(f"来自 {man.get('player')} @ {man.get('time')}    {man.get('summary', '')}")
    try:
        info(f"打包时间：{datetime.fromtimestamp(chosen.stat().st_mtime):%Y-%m-%d %H:%M}")
    except OSError:
        pass

    if wd.exists() and any(wd.iterdir()):
        if not force and not ask_yes("会覆盖本地存档（先自动备份），继续？", False):
            die("已取消（想直接覆盖：pull --from <目录> --force）")
        backup_world(wd, f"before-import-{cfg['player']}")

    tmp = TOOL_DIR / "_import_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(chosen) as zf:
        zf.extractall(tmp)

    changed = mirror(tmp, wd, ignore={"_mcmesh_chars", "mcmesh-manifest.json"})
    ok(f"已覆盖本地存档（{changed} 个文件更新）")

    # 角色记录一起搬过来（角色移植要用）
    moved = 0
    ch_src = tmp / "_mcmesh_chars"
    if ch_src.is_dir():
        for f in sorted(ch_src.glob("*.dat")):
            payload = None
            try:
                payload = nbt_unwrap(f.read_bytes())
            except Exception:
                payload = None
            if payload is None:
                continue
            save_char(cfg, f.stem, payload, set_owner=False)
            moved += 1
    if moved:
        info(f"顺带导入 {moved} 份角色记录到 chars/")

    shutil.rmtree(tmp, ignore_errors=True)
    st = load_state()
    st["last_synced"] = fingerprint(wd)
    st["last_pull"] = now_str()
    st["last_import"] = now_str()
    save_state(st)
    info(f"内容：{world_summary(cfg)}")


# ────────────────────────────────────────────────── 从别的世界导入玩家数据

# 各版本 level.dat / playerdata 里的 DataVersion（用来把"更新版本"的数据降一档）
DATAVERSIONS = {
    "1.20.4": 3700, "1.20.5": 3837, "1.20.6": 3839,
    "1.21": 3953, "1.21.1": 3955, "1.21.2": 4080, "1.21.3": 4082,
    "1.21.4": 4189, "1.21.5": 4325, "1.21.6": 4435, "1.21.7": 4438, "1.21.8": 4440,
}


def target_data_version(cfg):
    return DATAVERSIONS.get(str(cfg.get("mc_version", "")).strip())


def set_dataversion(payload: bytes, version: int) -> bytes:
    """把角色数据里的 DataVersion 改成目标版本（只动这 4 个字节）。"""
    raw = nbt_wrap(payload)
    entry = nbt_find(raw, ("DataVersion",))
    if not entry or entry[2] != 3:
        return payload
    new = raw[:entry[3]] + struct.pack(">i", version) + raw[entry[4]:]
    try:
        return nbt_unwrap(new)
    except Exception:
        return payload


def load_usercache(world: Path) -> dict[str, str]:
    """从 usercache.json 里拿 uuid -> 名字（服务端世界目录里通常有一份）。"""
    maps: dict[str, str] = {}
    for cand in (world / "usercache.json", world.parent / "usercache.json",
                 world.parent.parent / "usercache.json"):
        if not cand.is_file():
            continue
        try:
            rows = json.loads(cand.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(rows, dict):
            rows = [rows]
        for e in rows:
            if isinstance(e, dict) and e.get("uuid") and e.get("name"):
                maps[str(e["uuid"]).lower()] = str(e["name"])
    return maps


def uuid_name_map(cfg) -> dict[str, str]:
    """UUID -> 名字，包含 aliases（同一个人的另一个 UUID，比如正版号/客户端号）。"""
    out: dict[str, str] = {}
    for name, e in load_players(cfg)["players"].items():
        uid = str(e.get("uuid") or "").lower()
        if uid:
            out[uid] = name
        for a in e.get("aliases") or []:
            a = str(a).lower()
            if a:
                out.setdefault(a, name)
    return out


def add_alias(cfg, name: str, uid: str, note: str = "") -> bool:
    """给某个名字加一个别名 UUID（离线号 + 客户端号 同时指向同一个人）。"""
    uid = str(uid).strip().lower()
    if not uid:
        return False
    data = load_players(cfg)
    entry = data["players"].get(name)
    if not entry:
        return False
    if str(entry.get("uuid", "")).lower() == uid:
        return False
    aliases = [str(a).lower() for a in entry.get("aliases") or []]
    if uid in aliases:
        return False
    aliases.append(uid)
    entry["aliases"] = aliases
    if note:
        entry["note"] = (str(entry.get("note", "")) + " | " + note).strip(" |")
    save_players(cfg, data)
    return True


def auto_register_by_content(cfg, world: Path) -> list[str]:
    """playerdata 里某份身体和 chars/<某人>.dat 一模一样 → 把这个 UUID 记成他的别名。

    这样即使游戏给访客分配的是"客户端 UUID"（正版号那种随机值），只要身体能对上，
    也能认出来是谁，不需要手工登记。
    """
    pd = world / "playerdata"
    if not pd.is_dir():
        return []
    bodies: dict[bytes, str] = {}
    for p in chars_dir(cfg).glob("*.dat"):
        try:
            bodies[nbt_unwrap(p.read_bytes())] = p.stem
        except Exception:
            pass
    known = uuid_name_map(cfg)
    added = []
    for f in pd.glob("*.dat"):
        uid = f.stem.lower()
        if uid in known:
            continue
        payload = read_player_dat(f)
        if payload is None:
            continue
        name = bodies.get(payload)
        if not name:
            continue
        if name not in load_players(cfg)["players"]:
            register_player(cfg, name, "member", "按内容自动识别")
        if add_alias(cfg, name, uid, "自动识别的另一个UUID"):
            known[uid] = name
            added.append(f"{name}({uid[:8]}…)")
    return added


def auto_register_from_usercache(cfg, world: Path) -> list[str]:
    """存档旁边如果有 usercache.json（服务端世界常见），就把里面的名字-UUID 自动登记。"""
    maps = load_usercache(world)
    if not maps:
        return []
    known = {str(v.get("uuid", "")).lower() for v in load_players(cfg)["players"].values()}
    added = []
    for uid, name in maps.items():
        if uid in known:
            continue
        register_player(cfg, name, "member", "从 usercache 自动登记", uid=uid)
        added.append(name)
    return added


def cmd_import_players(args) -> None:
    cfg = load_cfg()
    src_arg = getattr(args, "from_world", None)
    if not src_arg:
        die('要指定来源世界，例如：\n  python mcmesh.py import-players --from "C:\\Users\\lanld\\Desktop\\mc服务器\\world"')
    src = _normalize_dir(src_arg)
    if not (src / "playerdata").is_dir():
        die(f"{src} 里没有 playerdata 目录，不像是世界文件夹")
    wd = world_dir(cfg)
    if not (wd / "level.dat").exists():
        die("本地还没有存档。先在游戏里建一个世界（进去站一下再退出），然后再导入")

    names = load_usercache(src)
    for n, e in load_players(cfg)["players"].items():
        uid = str(e.get("uuid") or "").lower()
        if uid:
            names.setdefault(uid, n)

    tgt_ver = target_data_version(cfg)
    src_ver = read_level_dat(src / "level.dat").get("Data", {}).get("DataVersion")
    say("== 导入玩家数据 ==")
    info(f"来源：{src}")
    info(f"目标：{wd}")
    info(f"DataVersion：来源 {src_ver} → 目标 {tgt_ver}")
    if tgt_ver and isinstance(src_ver, int) and src_ver > tgt_ver:
        warn("来源比目标新：新版才有的方块/物品会被丢掉，我会把这些角色数据的 DataVersion 改成目标版本")
    say()

    rows, copied, skipped = [], 0, 0
    for f in sorted((src / "playerdata").glob("*.dat")):
        if f.name.endswith(".dat_old"):
            continue
        uid = f.stem.lower()
        name = names.get(uid, "")
        payload = read_player_dat(f)
        if payload is None:
            rows.append((name or "（名字未知）", uid, "读不出来", "✗"))
            continue
        dv = parse_nbt(nbt_wrap(payload)).get("DataVersion")
        if tgt_ver and isinstance(dv, int) and dv > tgt_ver:
            payload = set_dataversion(payload, tgt_ver)
        d = parse_nbt(nbt_wrap(payload))
        inv = len(d.get("Inventory") or [])
        summary = f"等级 {d.get('XpLevel', 0)} · 背包 {inv} 件 · 末影箱 {len(d.get('EnderItems') or [])} 件"

        dst = wd / "playerdata" / f"{uid}.dat"
        (wd / "playerdata").mkdir(exist_ok=True)
        if dst.exists() and not args.force:
            rows.append((name or "（名字未知）", uid, summary, "已存在，跳过"))
            skipped += 1
            continue
        overwrote = dst.exists()
        write_player_dat(dst, payload)
        for sub in ("stats", "advancements"):
            s = src / sub / f"{uid}.json"
            if s.is_file():
                (wd / sub).mkdir(exist_ok=True)
                try:
                    shutil.copy2(s, wd / sub / f"{uid}.json")
                except OSError:
                    pass
        copied += 1
        rows.append((name or "（名字未知）", uid, summary, "覆盖" if overwrote else "导入"))
        if name:
            register_player(cfg, name, "member", "从旧世界导入")

    for name, uid, summary, status in rows:
        say(f"  {status:12} {name:14} {uid}  {summary}")
    if not rows:
        warn("来源世界里没有找到任何玩家数据")
    say()
    ok(f"完成：导入 {copied} 个，跳过 {skipped} 个")
    if any("名字未知" in r[0] for r in rows):
        info("名字未知的那几个，只有用对应名字进来才会生效（离线 UUID 必须能对上）")
    info("这些人的背包/坐标/成就会在下次进这个世界时生效")
    info("顺便说：世界本身（建筑、地形）不会跟着来，那些在地图 region 文件里，属于另一个世界")


def world_summary(cfg) -> str:
    wd = world_dir(cfg)
    if not wd.exists():
        return "（本地还没有存档）"
    n, size = dir_stats(wd)
    parts = [f"{human_size(size)} / {n} 个文件"]
    data = read_level_dat(wd / "level.dat")
    dat = data.get("Data", {}) if isinstance(data, dict) else {}
    if isinstance(dat, dict) and dat:
        name = dat.get("LevelName")
        day = dat.get("DayTime")
        if name:
            parts.append(f"世界名「{name}」")
        if isinstance(day, (int, float)) and day:
            parts.append(f"第 {int(day) // 24000 + 1} 天")
        ver = dat.get("Version", {})
        if isinstance(ver, dict) and ver.get("Name"):
            parts.append(str(ver["Name"]))
    pdata = wd / "playerdata"
    if pdata.exists():
        parts.append(f"{len(list(pdata.glob('*.dat')))} 个玩家数据")
    return " · ".join(parts)


# ────────────────────────────────────────────────── 实例探测

def guess_instances() -> list[Path]:
    home = Path.home()
    bases = [
        home / "Downloads" / ".minecraft",
        home / "Desktop" / ".minecraft",
        home / ".minecraft",
        home / "curseforge" / "minecraft",
        home / "Documents" / "curseforge" / "minecraft",
    ]
    found: list[Path] = []
    for base in bases:
        versions = base / "versions"
        if versions.is_dir():
            for d in sorted(versions.iterdir()):
                if d.is_dir() and ((d / "mods").is_dir() or (d / "saves").is_dir()):
                    found.append(d)
        inst = base / "Instances"
        if inst.is_dir():
            for d in sorted(inst.iterdir()):
                if d.is_dir():
                    found.append(d)
    seen, uniq = set(), []
    for p in found:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def mod_count(path: Path) -> int:
    m = path / "mods"
    return len(list(m.glob("*.jar"))) if m.is_dir() else 0


def ram_info() -> tuple[float, float]:
    """返回 (总内存 GB, 可用内存 GB)。非 Windows 就返回 (0, 0)。"""
    if sys.platform != "win32":
        return 0.0, 0.0
    try:
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MemStatus()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return 0.0, 0.0
        return st.ullTotalPhys / (1024 ** 3), st.ullAvailPhys / (1024 ** 3)
    except Exception:
        return 0.0, 0.0


def suggest_memory(total_gb: float) -> str:
    """模组服的建议堆上限：大概占物理内存 1/3，卡在 4~8G 之间。"""
    if total_gb <= 0:
        return "6G"
    gb = max(4, min(8, round(total_gb * 0.35)))
    return f"{gb}G"


def mem_gb(text: str) -> float:
    """把 "6G" / "6144M" 解析成 GB 数。"""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([gGmM]?)\s*$", str(text))
    if not m:
        return 0.0
    v = float(m.group(1))
    return v / 1024 if m.group(2).lower() == "m" else v


def guess_launcher() -> str:
    """找一个能自动打开的启动器 exe。"""
    home = Path.home()
    names = [
        "Plain Craft Launcher 2.exe", "PCL.exe", "PCL2.exe",
        "Hello Minecraft! Launcher.exe", "HMCL.exe", "MinecraftLauncher.exe",
    ]
    bases = [home / "Desktop", home / "Downloads", home / "Downloads" / ".minecraft",
             home / "Desktop" / ".minecraft", home / ".minecraft", home]
    for b in bases:
        for n in names:
            p = b / n
            if p.is_file():
                return str(p)
    for b in bases[:5]:
        try:
            for d in b.iterdir():
                if d.is_dir():
                    for n in names:
                        p = d / n
                        if p.is_file():
                            return str(p)
        except OSError:
            continue
    return ""


def detect_version(instance: Path) -> tuple[str, str]:
    mc, loader = "", ""
    ini = instance / "PCL" / "Setup.ini"
    if ini.exists():
        txt = ini.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^VersionVanillaName:(.+)$", txt, re.M)
        if m:
            mc = m.group(1).strip()
        m = re.search(r"^VersionFabric:(.+)$", txt, re.M)
        if m:
            loader = m.group(1).strip()
    return mc, loader


# ────────────────────────────────────────────────── 命令：init

def cmd_init(args) -> None:
    say("== 首次配置 MCMesh ==")
    say()

    cfg = load_cfg(required=False) or dict(DEFAULT_CONFIG)

    def arg(key: str, default: str = "") -> str:
        """命令行里给了就直接用，不再问（方便一行命令配好）。"""
        v = getattr(args, key, None)
        return str(v).strip() if v not in (None, "") else default

    # ① 玩家名
    name = arg("player")
    if name:
        info(f"玩家名：{name}")
    else:
        say("① 你在这个世界里的名字（进游戏用的 ID；离线模式靠它认人，之后别改）")
        name = ask("   玩家名", cfg.get("player") or "")
        while not name:
            name = ask("   玩家名（必填）")
    cfg["player"] = name

    # ② 整合包目录
    say()
    say("② 你的整合包目录（里面有 mods/ config/ saves/ 的那一层）")
    cands = guess_instances()
    chosen = arg("instance") or cfg.get("instance") or ""
    if not arg("instance") and not chosen and cands:
        for i, p in enumerate(cands[:12], 1):
            info(f"{i:2}) {p.name}   [{mod_count(p)} mods]")
            info(f"      {p}")
        info(f"{len(cands[:12]) + 1:2}) 手动输入路径")
        sel = ask("   选一个编号", "1")
        if sel.isdigit() and 1 <= int(sel) <= len(cands[:12]):
            chosen = str(cands[int(sel) - 1])
    if not chosen:
        chosen = ask("   路径", str(cands[0]) if cands else "")
    p = Path(chosen)
    if not p.is_dir():
        die(f"目录不存在：{p}")
    cfg["instance"] = str(p)
    mc_ver, loader = detect_version(p)
    if mc_ver:
        cfg["mc_version"] = mc_ver
        info(f"识别到版本：{mc_ver}" + (f"（Fabric {loader}）" if loader else ""))
    if loader:
        cfg["fabric_loader"] = loader

    # ③ 模式
    say()
    say("③ 联机方式")
    info("1) lan    单人存档 + 对局域网开放（零服务端；换人开后新房主会接手上个房主的角色）")
    info("2) server 生成 Fabric 专用服，轮到谁谁开（所有人身份独立，没有房主特权）")
    m = arg("mode")
    if m:
        info(f"联机方式：{m}")
    else:
        m = ask("   选 1 还是 2", "2" if cfg.get("mode") == "server" else "1")
    cfg["mode"] = "server" if str(m).strip().lower() in ("2", "server") else "lan"

    if cfg["mode"] == "server":
        mem = arg("memory")
        if mem:
            cfg["server_memory"] = mem
            info(f"服务端内存上限：-Xmx{mem}")
        else:
            total, avail = ram_info()
            info(f"这台机器 {total:.0f} GB 内存（当前可用 {avail:.0f} GB）")
            cfg["server_memory"] = ask("   给服务端的内存上限 -Xmx", suggest_memory(total))

    # ④ 存档
    say()
    saves = p / "saves"
    existing = sorted(d.name for d in saves.iterdir()) if saves.is_dir() else []
    if cfg["mode"] == "lan":
        w = arg("world")
        if w:
            cfg["world"] = w
            info(f"存档文件夹名：{w}")
        else:
            if existing:
                info("现有存档：" + "、".join(existing))
            default_world = (cfg.get("world") if cfg.get("world") in existing
                             else (existing[0] if existing else "BetterMC"))
            cfg["world"] = ask("   要用/新建哪个存档文件夹名", default_world)
    else:
        cfg["world"] = "world"

    # ⑤ 启动器（可选，能自动帮你打开游戏）
    say()
    say("⑤ 启动器（可选）：能自动帮你把游戏打开")
    forced = arg("launcher")
    if forced:
        if Path(forced).exists():
            cfg["launcher_exe"] = forced
            info(f"启动器：{forced}")
        else:
            warn(f"这个路径不存在，先跳过：{forced}")
    elif getattr(args, "no_launcher", False):
        info("按参数要求跳过自动启动器")
    else:
        exe = cfg.get("launcher_exe") or guess_launcher()
        if exe:
            info(f"找到启动器：{exe}")
            if ask_yes("   以后用它自动打开游戏？", True):
                cfg["launcher_exe"] = exe
        else:
            info("没找到启动器 exe（跳过也行，手动打开游戏即可）")
            v = ask("   启动器 exe 路径（可留空）", "")
            if v and Path(v).exists():
                cfg["launcher_exe"] = v
            elif v:
                info("这个路径不存在，先跳过")

    # ⑥ 仓库
    say()
    say("⑥ 存档仓库（GitHub 私有仓库；没有就留空，我先建本地仓库）")
    repo = arg("repo")
    if repo:
        cfg["repo_url"] = repo
        info(f"仓库地址：{repo}")
    else:
        cfg["repo_url"] = ask("   仓库地址", cfg.get("repo_url") or "")
    br = arg("branch")
    if br:
        cfg["branch"] = br
        info(f"分支：{br}")
    else:
        cfg["branch"] = ask("   分支名", cfg.get("branch") or "main") or "main"
    save_cfg(cfg)

    rd = repo_dir(cfg)
    if cfg["repo_url"] and not (rd / ".git").exists():
        if rd.exists() and any(rd.iterdir()):
            warn(f"{rd} 已存在且非空，不自动克隆")
        else:
            rd.parent.mkdir(parents=True, exist_ok=True)
            info(f"克隆 {cfg['repo_url']} -> {rd}")
            r = run(["git", "clone", cfg["repo_url"], str(rd)], check=False)
            if r.returncode != 0:
                warn("克隆失败（空仓库没初始化过 / 没权限 / 地址不对）")
                warn((((r.stderr or "") + (r.stdout or "")).strip())[:300])
                if ask_yes("改成在这里建一个本地仓库，把远端地址记下来？", True):
                    init_local_repo(cfg)
    elif not (rd / ".git").exists():
        init_local_repo(cfg)

    # 本地仓库已就绪但还没连远端时，把 origin 补上（第一次 push 会弹浏览器让你登录）
    if (rd / ".git").exists() and cfg.get("repo_url"):
        has_origin = bool(git_out(cfg, "remote", "get-url", "origin"))
        if not has_origin:
            run(["git", "remote", "add", "origin", cfg["repo_url"]], cwd=rd, check=False)
            ok(f"已记录远端地址：{cfg['repo_url']}")

    if (rd / ".git").exists():
        seed_repo_files(cfg)
        ensure_git_identity(cfg)
        register_player(cfg, cfg["player"], "owner", "本机")
        if not git_out(cfg, "rev-parse", "--verify", "--quiet", "HEAD"):
            git(cfg, "add", "-A")
            git(cfg, "commit", "-m", "初始化：MCMesh 存档仓库")
            ok("已在本地完成首次提交")

    save_state({"last_synced": fingerprint(world_dir(cfg)), "last_pull": "", "last_push": ""})
    say()
    ok(f"配置写好了：{CONFIG_PATH}")
    next_steps(cfg)


def init_local_repo(cfg) -> None:
    rd = repo_dir(cfg)
    rd.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-b", cfg["branch"]], cwd=rd, check=False)
    ok(f"已初始化本地仓库：{rd}")


def seed_repo_files(cfg) -> None:
    rd = repo_dir(cfg)
    gi = rd / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "session.lock\n*.lock\n"
            "server/logs/\nserver/crash-reports/\n"
            "server/usercache.json\nserver/version_history.json\n",
            encoding="utf-8",
        )
    rm = rd / "README.md"
    if not rm.exists():
        rm.write_text(
            "# 我的世界存档仓库\n\n"
            "这个仓库由 MCMesh 维护：玩完 `mcmesh.py push` 提交，开玩前 `mcmesh.py pull` 拉取。\n\n"
            "成员身份记录在 `players.json`（离线模式下 UUID 由玩家名推导）。\n",
            encoding="utf-8",
        )
    if cfg["mode"] == "lan":
        (rd / "world").mkdir(exist_ok=True)
    else:
        (rd / "server" / "world").mkdir(parents=True, exist_ok=True)


def next_steps(cfg) -> None:
    say()
    say("接下来：")
    if cfg["repo_url"]:
        info("· GitHub 仓库 Settings → Collaborators 里把朋友都邀请进来")
        info("· 第一次 push 会弹浏览器登录 GitHub，登一次就好")
    else:
        info("· 去 GitHub 新建一个 Private 仓库（别勾 README），然后：")
        info(f'  cd "{repo_dir(cfg)}"')
        info("  git remote add origin https://github.com/你的用户名/仓库名.git")
        info(f"  git push -u origin {cfg['branch']}")
    if cfg["mode"] == "lan":
        info(f"· 存档目录：{world_dir(cfg)}")
        info("· 开始玩：python mcmesh.py host    玩完回窗口按回车，自动上传")
    else:
        info("· 先生成服务端：python mcmesh.py serverpack")
        info("· 之后开服：python mcmesh.py host    关服后自动上传")


# ────────────────────────────────────────────────── 命令：status / doctor

def cmd_status(args) -> None:
    cfg = load_cfg()
    st = load_state()
    say("== 当前状态 ==")
    mode = "单人存档+局域网" if cfg["mode"] == "lan" else "Fabric 专用服"
    info(f"玩家：{cfg['player']}   模式：{mode}")
    info(f"整合包：{cfg['instance']}")
    info(f"存档：{world_dir(cfg)}")
    info(f"内容：{world_summary(cfg)}")
    if (repo_dir(cfg) / ".git").exists():
        if cfg.get("repo_url"):
            git_fetch(cfg, soft=True)
        info(f"最近上传：{last_commit_info(cfg, world_git_path(cfg))}")
        local = local_head(cfg)
        remote = remote_head(cfg) if cfg.get("repo_url") else ""
        if not cfg.get("repo_url"):
            info("只在本机同步（还没配远端仓库）")
        elif not remote:
            info("远端仓库还是空的（等你第一次上传）")
        elif not local:
            warn("本地还没有提交 —— 直接 push 就行")
        elif local == remote:
            info("本地与远端：一致")
        elif is_ancestor(cfg, local, remote):
            warn("远端有更新的存档 —— 开玩前先 pull")
        elif is_ancestor(cfg, remote, local):
            warn("本地有还没上传的存档 —— 玩完记得 push")
        else:
            warn("本地和远端分叉了 —— 先 pull，或 push --force 覆盖远端")
        if repo_dirty(cfg):
            warn("仓库有未提交的改动")
    cur = fingerprint(world_dir(cfg))
    if st.get("last_synced") and cur and cur != st["last_synced"]:
        warn("本地存档和你上次同步时不一样（可能是玩过还没上传）")
    info(f"最近 pull：{st.get('last_pull') or '（无记录）'}   最近 push：{st.get('last_push') or '（无记录）'}")

    # 成就绑定 + 管理员开关（这两件事"改了没效果"基本都是被游戏写回去了）
    wd = world_dir(cfg)
    if (wd / "level.dat").exists():
        live = live_player_uuid(cfg, wd, remember=False)
        f = wd / "advancements" / f"{live}.json"
        n = _aux_done(f) if f.is_file() else -1
        say()
        info(f"账号身份（游戏在用）：{live or '（读不出）'}")
        if n >= 0:
            info(f"这个身份已解锁成就：{n} 条")
        owner = identify_payload_owner(cfg, get_host_char_payload(wd)) or st.get("host_char_owner") or ""
        if owner:
            best = best_aux_source(cfg, owner, wd, "advancements")
            bn = _aux_done(best) if best is not None else -1
            if bn > n >= 0:
                warn(f"「{owner}」档案里有 {bn} 条成就，比游戏里这份多 —— 退出游戏后跑 fix 绑上")
        flag = allow_commands(wd)
        info(f"「允许作弊」（管理员权限）：{'打开' if flag == 1 else '关闭 —— /gamemode 用不了'}")
        if flag != 1:
            info("   退出游戏后：python mcmesh.py op（或界面右下「作弊开关」）")


def cmd_doctor(args) -> None:
    cfg = load_cfg(required=False)
    say("== 体检 ==")
    info(f"Python {sys.version.split()[0]}（需要 3.8+）")
    r = run(["git", "--version"], check=False)
    if r.returncode == 0:
        ok((r.stdout or "").strip())
    else:
        warn("没装 Git：https://git-scm.com/download/win （装的时候一路默认）")
    if not cfg:
        warn("还没有配置，先跑 python mcmesh.py init")
        return
    for label, path in (("整合包目录", instance_dir(cfg)), ("仓库目录", repo_dir(cfg)), ("存档目录", world_dir(cfg))):
        if path.exists():
            ok(f"{label}存在：{path}")
        else:
            warn(f"{label}不存在：{path}")
    n, size = dir_stats(world_dir(cfg))
    info(f"存档体积：{human_size(size)}，{n} 个文件")
    if size > 1200 * 1024 * 1024:
        warn("存档超过 1.2 GB，GitHub 会比较吃力")
    wd = world_dir(cfg)
    if wd.exists():
        for root, _d, files in os.walk(wd):
            for f in files:
                fp = Path(root) / f
                try:
                    if fp.stat().st_size > 80 * 1024 * 1024:
                        warn(f"单文件超过 80MB（GitHub 上限 100MB）：{fp}")
                except OSError:
                    pass
    if cfg["mode"] == "lan":
        total, avail = ram_info()
        if total:
            info(f"内存：共 {total:.0f} GB，现在可用 {avail:.0f} GB")
        info("局域网模式没有单独的服务端进程，内存全在客户端那一个 JVM 里")
        info("这个 333 mod 的包：单人 6G，房主 + 2~3 人 8G（PCL：版本设置 → 游戏内存）")
    if cfg["mode"] == "server":
        total, avail = ram_info()
        if total:
            info(f"内存：共 {total:.0f} GB，现在可用 {avail:.0f} GB，"
                 f"服务端上限 -Xmx{cfg['server_memory']}（建议 {suggest_memory(total)}）")
        j = find_java(21)
        if j:
            ok(f"Java 21+：{j}")
        else:
            warn("没找到 Java 21，1.21.1 服务端需要它")
        if not (repo_dir(cfg) / "server" / "fabric-server-launch.jar").exists():
            warn("还没生成服务端，跑 python mcmesh.py serverpack")
    if cfg.get("player"):
        ok(f"你的离线身份：{cfg['player']} -> {offline_uuid(cfg['player'])}（改名字=换人）")
    # 认不出来的玩家数据
    pd = world_dir(cfg) / "playerdata"
    if pd.is_dir():
        known = {str(v.get("uuid", "")).lower() for v in load_players(cfg)["players"].values()}
        host = get_host_char_payload(world_dir(cfg))
        unknown = []
        for f in pd.glob("*.dat"):
            if f.stem.lower() in known:
                continue
            if host is not None and read_player_dat(f) == host:
                continue  # 房主自己在客户端 UUID 下的副本
            unknown.append(f.stem)
        if unknown:
            warn(f"有 {len(unknown)} 个玩家数据认不出来（名字→UUID 是单向哈希，反推不了）")
            info("离线号：python mcmesh.py players --add 名字")
            info("正版号：python mcmesh.py players --add 名字 --uuid <那串UUID>")


# ────────────────────────────────────────────────── 命令：pull / push

def ensure_world_configured(cfg) -> None:
    """游戏里新建存档后名字可能和配置不一致，这里顺手纠正一次。"""
    if cfg["mode"] != "lan":
        return
    if world_dir(cfg).exists():
        return
    saves = instance_dir(cfg) / "saves"
    cands = sorted(d for d in saves.iterdir() if d.is_dir()) if saves.is_dir() else []
    if len(cands) == 1 and ask_yes(
        f"配置里的存档叫「{cfg['world']}」但不存在，改用「{cands[0].name}」？", True
    ):
        cfg["world"] = cands[0].name
        save_cfg(cfg)
        ok(f"存档名已改成「{cfg['world']}」")


def cmd_pull(args) -> None:
    cfg = load_cfg()

    # --from：从 U 盘 / 任意文件夹导入，不碰 Git
    from_dir = getattr(args, "from_dir", None)
    if from_dir is not None:
        say("== 从文件夹导入存档 ==")
        src = resolve_dir(cfg, from_dir, "usb_dir", "从哪个文件夹导入", create=False)
        import_zip(cfg, src, force=getattr(args, "force", False))
        return

    ensure_git_repo(cfg)
    ensure_world_configured(cfg)
    st = load_state()
    wd = world_dir(cfg)

    say("== 拉取最新存档 ==")
    if cfg["mode"] == "lan" and recently_written(wd):
        warn("本地存档 45 秒内还在被写入 —— 游戏是不是还开着？")
        info("先退出游戏再拉取，否则可能把正在用的存档覆盖掉")
        if not args.force and not ask_yes("仍然继续？", False):
            die("已取消（确认游戏退了就加 --force 跳过这个提问）")
    if repo_dirty(cfg):
        # lan 模式下仓库里只是存档的副本，丢了也无所谓；server 模式下那就是真存档
        if cfg["mode"] == "server" and not args.force:
            die("服务端存档有未提交的改动（上次没传完？）。先 push，或加 --force 丢弃")
        if cfg["mode"] == "server" and world_dir(cfg).exists():
            backup_world(world_dir(cfg), f"before-pull-{cfg['player']}")
        if local_head(cfg):
            git(cfg, "reset", "--hard", "HEAD")
        warn("已清理仓库里未提交的改动")

    if cfg.get("repo_url"):
        fetched = git_fetch(cfg, soft=True)
        remote = remote_head(cfg)
        head = local_head(cfg)
        if remote and head and is_ancestor(cfg, remote, head) and head != remote:
            # 本地比远端新：说明之前的上传根本没推上去（断网等），绝不能 reset 掉
            warn("本地仓库比远端新（上次的推送没成功）—— 不覆盖本地，就用本地这份")
            info("网络好了之后按 2 上传一次就行")
        elif remote:
            info(f"远端最新：{last_commit_info(cfg, world_git_path(cfg))}")
            if not fetched:
                info("（这次没能确认远端是不是更新的，用的是本地仓库里存的那份）")
            git(cfg, "reset", "--hard", f"origin/{cfg['branch']}")
        else:
            warn("远端还没有存档（第一次上传之前就是这样）")
    ok(f"仓库已更新到 {(local_head(cfg) or '')[:7] or '（空仓库）'}")

    if cfg["mode"] == "lan":
        src = repo_world_dir(cfg)
        if not src.exists() or not any(src.iterdir()):
            warn("远端还没有存档 —— 这次直接在游戏里新建一个世界就行")
            info("（不会在 saves 里留空文件夹，你放心建）")
        else:
            cur = fingerprint(wd)
            if wd.exists() and cur and st.get("last_synced") and cur != st["last_synced"]:
                warn("本地存档和你上次同步时不一样，可能玩过还没上传")
                if not args.force and not ask_yes("继续会覆盖这些进度（会先备份）。确定？", False):
                    die("已取消（想强制覆盖就加 --force）")
            if wd.exists():
                backup_world(wd, f"before-pull-{cfg['player']}")
                n = mirror(src, wd)
                ok(f"已覆盖本地存档（{n} 个文件更新）")
            else:
                mirror(src, wd)
                ok("本地存档已就位")
    else:
        n, size = dir_stats(wd)
        if wd.exists():
            info(f"服务端存档：{human_size(size)} / {n} 个文件")

    st["last_synced"] = fingerprint(wd)
    st["last_pull"] = now_str()
    save_state(st)
    info(f"内容：{world_summary(cfg)}")


def cmd_push(args) -> None:
    cfg = load_cfg()

    # --to：导出到 U 盘 / 任意文件夹，不碰 Git
    to_dir = getattr(args, "to_dir", None)
    if to_dir is not None:
        say("== 导出存档到文件夹 ==")
        dest = resolve_dir(cfg, to_dir, "usb_dir", "导出到哪个文件夹")
        export_zip(cfg, dest)
        return

    ensure_git_repo(cfg)
    ensure_world_configured(cfg)
    st = load_state()
    wd = world_dir(cfg)
    if not wd.exists() or not any(wd.iterdir()):
        saves = instance_dir(cfg) / "saves"
        hint = []
        if saves.is_dir():
            hint = [d.name for d in saves.iterdir() if d.is_dir()]
        msg = f"找不到存档：{wd}"
        if hint:
            msg += f"\nsaves 目录里现有的存档：{'、'.join(hint)}"
            msg += "\n把 config.json 里的 \"world\" 改成正确的名字再试"
        die(msg)

    if recently_written(wd):
        warn("存档 45 秒内还在被写入 —— 如果游戏还开着，先退出游戏再上传")
    say("== 上传存档 ==")
    if cfg["mode"] == "lan":
        n = mirror(wd, repo_world_dir(cfg))
        info(f"复制了 {n} 个文件到仓库")
        # 存档里现在装着谁的身体，这次的进度就该记到谁名下；顺便把成就/统计一起存
        detect_local_uuid(wd)
        if (st.get("host_char_owner") or cfg["player"]) != cfg["player"]:
            warn(f"你现在用的是「{st.get('host_char_owner')}」的角色 —— 这次的进度会记到他名下")
            info(f"（想用自己的角色玩完再传，先：python mcmesh.py chars --use {cfg['player']}）")
        owner = stash_current_host_char(cfg, wd, quiet=True)
        if owner:
            info(f"记下角色「{owner}」：{char_summary(get_host_char_payload(wd))}")
        newnames = auto_map_from_logs(cfg, force=True)
        if newnames:
            info("从日志新识别到：" + "、".join(sorted(set(newnames))))
        guests = import_guest_chars(cfg, wd, skip=owner or cfg["player"])
        if guests:
            info("顺带记下访客角色：" + "、".join(guests))
        auto = auto_register_from_usercache(cfg, wd)
        if auto:
            info("从 usercache.json 自动登记了：" + "、".join(auto))
        byc = auto_register_by_content(cfg, wd)
        if byc:
            info("按身体内容自动识别出：" + "、".join(byc))
    msg = args.message or "存档更新"
    if commit_and_push(cfg, msg, force=args.force):
        st["last_synced"] = fingerprint(wd)
        st["last_push"] = now_str()
        save_state(st)
        info(f"内容：{world_summary(cfg)}")


def cmd_backup(args) -> None:
    cfg = load_cfg()
    b = backup_world(world_dir(cfg), f"manual-{cfg['player']}")
    ok(f"备份完成：{b}")


# ────────────────────────────────────────────────── 命令：host / play

def find_java(min_major: int = 21) -> str:
    cands = []
    jh = os.environ.get("JAVA_HOME", "")
    if jh:
        cands.append(Path(jh) / "bin" / "java.exe")
    for base in (r"C:\Program Files\Java", r"C:\Program Files\Eclipse Adoptium",
                 r"C:\Program Files\Microsoft", r"C:\Program Files\Zulu"):
        try:
            for d in sorted(Path(base).iterdir(), reverse=True):
                exe = d / "bin" / "java.exe"
                if exe.exists():
                    cands.append(exe)
        except OSError:
            pass
    which = shutil.which("java")
    if which:
        cands.append(Path(which))
    for exe in cands:
        try:
            p = run([exe, "-version"], check=False)
            txt = (p.stderr or "") + (p.stdout or "")
            m = re.search(r'version "(\d+)(?:\.(\d+))?', txt)
            if m:
                major = int(m.group(1))
                if major == 1:
                    major = int(m.group(2) or 0)
                if major >= min_major:
                    return str(exe)
        except Exception:
            continue
    return ""


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def launch_client(cfg) -> None:
    exe = cfg.get("launcher_exe")
    if exe and Path(exe).exists():
        info(f"启动启动器：{exe}")
        subprocess.Popen([str(exe)], cwd=str(Path(exe).parent))
    else:
        say()
        warn("没有配置启动器路径，请自己打开 PCL / HMCL")
        info("想让它自动打开：在 config.json 里填 launcher_exe（启动器 exe 的完整路径）")
        info(f"整合包目录：{cfg['instance']}")


def cmd_play(args) -> None:
    cfg = load_cfg()
    cmd_pull(argparse.Namespace(force=getattr(args, "force", False)))
    say()
    launch_client(cfg)
    if cfg["mode"] == "lan":
        say()
        info("进游戏 → 单人游戏 → 选存档。要让别人进来：ESC → 对局域网开放")


def cmd_host(args) -> None:
    cfg = load_cfg()
    if cfg["mode"] == "server":
        host_server(cfg)
    else:
        host_lan(cfg)


def local_dirty_vs_sync(cfg) -> bool:
    """本地存档和"上次同步完成时"是不是不一样（= 有还没上传的进度）。"""
    st = load_state()
    wd = world_dir(cfg)
    last = st.get("last_synced") or ""
    if not last or not wd.exists():
        return False
    return fingerprint(wd) != last


def host_lan(cfg) -> None:
    wd = world_dir(cfg)
    dirty = local_dirty_vs_sync(cfg)
    if dirty:
        say("== 开局（本地优先）==")
        warn("你本地有还没上传的进度 —— 这次不拉取，直接用本地这份存档开局")
        info("（想先同步别人的最新进度：先按 2 上传，再按 3 拉取；或者直接按 1 也能玩）")
    else:
        cmd_pull(argparse.Namespace(force=False))

    if (wd / "level.dat").exists():
        if dirty:
            info("本地存档里的角色是你上次玩出来的，这次不做替换")
        else:
            payload = load_char(cfg, cfg["player"])
            if payload is not None:
                if get_host_char_payload(wd) != payload:
                    # 换之前先把现在这具身体存回去，别丢上一个房主的进度
                    detect_local_uuid(wd)
                    stash_current_host_char(cfg, wd, quiet=True)
                    how = set_host_char_payload(wd, payload)
                    ok(f"已换上你自己的角色（{how}）：{char_summary(payload)}")
                    aux = apply_char_aux(cfg, cfg["player"], wd)
                    if aux:
                        info(f"成就/统计也换成你自己的：{'、'.join(aux)}")
                    for note in sync_local_identity_body(cfg, wd, payload, name=cfg["player"]):
                        info(f"     {note}")
                    for note in mirror_body_to_guest(cfg, wd, cfg["player"], payload):
                        info(f"     {note}")
                else:
                    info("存档里的房主角色就是你，不用换")
            else:
                warn("还没有你自己的角色记录 —— 这次你会以存档里那个角色出生")
                info(f"玩完上传时会自动把它记成「{cfg['player']}」")
            # 换完角色立刻记一笔，免得下次开局被当成"有未上传进度"
            st = load_state()
            st["last_synced"] = fingerprint(wd)
            st["host_char_owner"] = cfg["player"]
            save_state(st)
    say()
    say("== 现在轮到你当房主 ==")
    info("1. 游戏里：单人游戏 → 进存档")
    info(f"2. ESC → 对局域网开放，端口填 {cfg['server_port']} 最省事")
    info(f"3. 同一局域网的朋友：多人游戏 → 直接连接 → {local_ip()}:{cfg['server_port']}")
    say()
    launch_client(cfg)
    say()
    say("怎么结束：先退出游戏（存档会写盘），然后在这个窗口按回车，我帮你上传。")
    try:
        input(">>> 玩完按回车上传存档 ...")
    except EOFError:
        return
    say()
    cmd_push(argparse.Namespace(message=None, force=False))


def ensure_server_properties(cfg) -> Path:
    server = repo_dir(cfg) / "server"
    server.mkdir(parents=True, exist_ok=True)
    prop = server / "server.properties"
    desired = {
        "online-mode": "false",          # 有离线号的朋友，只能关验证；名字=身份
        "white-list": "true",            # 谁在 players.json 里，谁就能进
        "enforce-whitelist": "true",
        "server-port": str(cfg["server_port"]),
        "allow-flight": "true",          # 整合包里一堆载具/滑翔，不开会被踢
        "view-distance": "8",
        "simulation-distance": "8",
        "max-players": "12",
        "spawn-protection": "0",
        "enable-command-block": "true",
        "motd": "Better MC 一起玩",
        "sync-chunk-writes": "false",
    }
    existing = {}
    if prop.exists():
        for line in prop.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    changed = False
    for k, v in desired.items():
        if k not in existing:
            existing[k] = v
            changed = True
    if changed or not prop.exists():
        with open(prop, "w", encoding="utf-8") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
    if existing.get("online-mode", "true").lower() != "false":
        warn("server.properties 里 online-mode 不是 false，离线号的朋友会进不来")
    return prop


def host_server(cfg) -> None:
    ensure_git_repo(cfg)
    server = repo_dir(cfg) / "server"
    jar = server / "fabric-server-launch.jar"
    if not jar.exists():
        warn("还没生成服务端")
        if ask_yes("现在生成？（从 Fabric 官网下载 loader，几百 KB）", True):
            cmd_serverpack(argparse.Namespace(force=False, dry_run=False))
        else:
            die("先运行 python mcmesh.py serverpack")

    cmd_pull(argparse.Namespace(force=False))
    write_server_identity_files(cfg)
    ensure_server_properties(cfg)
    (server / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    java = find_java(21)
    if not java:
        die("找不到 Java 21+，1.21.1 服务端跑不起来")
    total, avail = ram_info()
    need = mem_gb(cfg["server_memory"])
    if total and need > avail - 1:
        warn(f"现在只剩 {avail:.0f} GB 可用内存，服务端要 {need:.0f}G，容易被系统拖死")
        info("建议先关掉浏览器/别的游戏，或者把 config.json 的 server_memory 调小")
    say()
    say("== 开服中 ==")
    info(f"本机局域网地址：{local_ip()}:{cfg['server_port']}")
    info("朋友们：多人游戏 → 直接连接 → 输入上面的地址")
    info("关服：在这个窗口输入 stop 回车（别直接点 X）")
    say()
    p = subprocess.run(
        [java, "-Xms2G", f"-Xmx{cfg['server_memory']}", "-XX:+UseG1GC",
         "-jar", str(jar), "nogui"],
        cwd=str(server),
    )
    say()
    info(f"服务端已退出（退出码 {p.returncode}）")
    cmd_push(argparse.Namespace(message=None, force=False))


# ────────────────────────────────────────────────── 命令：serverpack

def _meta_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "MCMesh"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_loader(mc: str, prefer: str) -> str:
    rows = _meta_json(f"{FABRIC_META}/versions/loader/{mc}")
    versions = [r["loader"]["version"] for r in rows]
    if prefer and prefer in versions:
        return prefer
    stable = [r["loader"]["version"] for r in rows if r["loader"].get("stable")]
    return stable[0] if stable else versions[0]


def mod_meta(jar: Path) -> tuple[str, str]:
    """返回 (environment, mod_id)；读不出来返回 ('', '')。"""
    try:
        with zipfile.ZipFile(jar) as z:
            try:
                raw = z.read("fabric.mod.json")
            except KeyError:
                return "", ""
        d = json.loads(raw.decode("utf-8", "replace"))
        return str(d.get("environment", "*")).lower(), str(d.get("id", ""))
    except Exception:
        return "", ""


def classify_mods(cfg) -> tuple[list[Path], list[tuple[Path, str]]]:
    inst = instance_dir(cfg)
    mods_dir = inst / "mods"
    if not mods_dir.is_dir():
        die(f"找不到 mods 目录：{mods_dir}")
    exclude_pats = [s.lower() for s in cfg.get("server_mods_exclude", [])]
    include_pats = [s.lower() for s in cfg.get("server_mods_include", [])]

    keep, dropped = [], []
    for jar in sorted(mods_dir.glob("*.jar")):
        low = jar.name.lower()
        if any(p in low for p in include_pats):
            keep.append(jar)
            continue
        if any(p in low for p in exclude_pats):
            dropped.append((jar, "配置里排除"))
            continue
        env, mid = mod_meta(jar)
        if env == "client":
            dropped.append((jar, "fabric.mod.json 标了 environment=client"))
            continue
        if env in ("", "*") and any(h in low or (mid and h in mid.lower()) for h in CLIENT_ONLY_HINTS):
            dropped.append((jar, "已知只能在客户端跑"))
            continue
        keep.append(jar)
    return keep, dropped


def cmd_serverpack(args) -> None:
    cfg = load_cfg()
    if cfg["mode"] != "server":
        warn("当前是 lan 模式；生成的服务端可以用，但存档同步仍按 lan 走")
    server = repo_dir(cfg) / "server"
    keep, dropped = classify_mods(cfg)

    say("== 生成 Fabric 服务端 ==")
    info(f"客户端 mods 共 {len(keep) + len(dropped)} 个：保留 {len(keep)}，排除 {len(dropped)}")
    for jar, why in dropped:
        info(f"  - {jar.name}   （{why}）")
    if args.dry_run:
        ok("（dry-run，没写任何文件）")
        return

    server.mkdir(parents=True, exist_ok=True)
    mods = server / "mods"
    mods.mkdir(exist_ok=True)
    for old in mods.glob("*.jar"):
        old.unlink()
    for jar in keep:
        shutil.copy2(jar, mods / jar.name)
    ok(f"已复制 {len(keep)} 个 mod 到 {mods}")

    for dname in ("config", "defaultconfigs", "configureddefaults", "kubejs"):
        src = instance_dir(cfg) / dname
        if src.is_dir():
            dst = server / dname
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            info(f"复制 {dname}/")

    jar = server / "fabric-server-launch.jar"
    if not jar.exists() or args.force:
        mc = cfg["mc_version"]
        try:
            installer = _meta_json(f"{FABRIC_META}/versions/installer")
            installer_ver = next(
                (i["version"] for i in installer if i.get("stable")), installer[0]["version"]
            )
            loader = resolve_loader(mc, cfg.get("fabric_loader", ""))
            url = f"{FABRIC_META}/versions/loader/{mc}/{loader}/{installer_ver}/server/jar"
            info(f"下载 Fabric loader {loader}（MC {mc}）...")
            req = urllib.request.Request(url, headers={"User-Agent": "MCMesh"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            jar.write_bytes(data)
            ok(f"已保存 {jar.name}（{human_size(len(data))}）")
        except Exception as e:
            warn(f"下载服务端失败：{type(e).__name__}: {e}")
            info("手动方案：https://fabricmc.net/use/server/ 下载对应版本，存成上面的 jar")
    else:
        info("服务端 jar 已存在，跳过下载")

    ensure_server_properties(cfg)
    write_server_identity_files(cfg)
    say()
    ok(f"服务端目录准备好了：{server}")
    info("以后开服：python mcmesh.py host")


# ────────────────────────────────────────────────── 命令：chars（角色移植）

def switch_character(cfg, name: str = "", payload: bytes = None,
                     force: bool = False, allow_running: bool = False) -> dict:
    """换角色（可给名字或直接给角色数据）。给 GUI / 命令行共用，不读 stdin。

    返回 {"ok": bool, "name": str, "how": str, "summary": str, "notes": [..]}
    """
    wd = world_dir(cfg)
    if payload is None:
        if not name:
            raise ValueError("要指定角色名字或数据")
        payload = load_char(cfg, name)
        if payload is None:
            raise ValueError(f"没有「{name}」的角色记录")
    if not (wd / "level.dat").exists():
        raise ValueError(f"本地还没有存档：{wd}")
    cur_now = get_host_char_payload(wd)
    if cur_now is not None and cur_now == payload:
        return {"ok": True, "name": name, "how": "无需切换",
                "summary": char_summary(payload),
                "notes": ["这个身份就是当前存档里的角色，没有变化"],
                "payload": payload, "unchanged": True}
    st0 = load_state()
    ignore_before = float(st0.get("last_tool_write") or 0)
    procs = java_processes()
    if procs and not allow_running:
        raise RuntimeError(
            "检测到 Minecraft 还在运行（" + "、".join(procs) + "）。\n"
            "停在主菜单也算在运行，level.dat 会被占住。\n"
            "→ 请在 PCL 里把游戏完全关掉，再换角色。")
    # 没有游戏进程时只犹豫 3 秒（游戏退出瞬间会写盘）。
    # 以前是 45 秒，导致"刚关掉游戏就换角色"被误判成失败。
    if not procs and recently_written(wd, seconds=3, ignore_before=ignore_before) and not allow_running:
        raise RuntimeError("存档 3 秒前刚被写过（可能游戏刚退出），请再等两秒重试")

    notes = []
    detect_local_uuid(wd)
    local_uuids = plausible_local_uuids(cfg, wd)          # 换之前先记下所有候选 UUID
    stash_current_host_char(cfg, wd)                      # ① 先存走当前这具
    how = set_host_char_payload(wd, payload)              # ② 换身体
    aux = apply_char_aux(cfg, name, wd) if name else []   # ③ 成就/统计
    if aux:
        notes.append("成就/统计也换过来了：" + "、".join(aux))
    elif name:
        notes.append("这个角色还没存过成就/统计（在那个角色下玩一局并上传就会补上）")
    notes += sync_local_identity_body(cfg, wd, payload, local_uuids, name)   # ④ 房主身份
    if name:
        notes += mirror_body_to_guest(cfg, wd, name, payload)  # ⑤ 访客身份
        save_char(cfg, name, payload)                          # 记下"房主现在是谁"
        mark_char_world(cfg, name, wd)                         # ⑥ 记下归属存档

    st = load_state()
    st["last_synced"] = fingerprint(wd)
    # 房主是谁：给了名字就用名字；否则按"身体指纹"重新认（列表里直接选身体时走这里）。
    # 不重新认的话，早先记错的名字会一直粘着，界面就会显示成别人的角色。
    st["host_char_owner"] = (name or identify_payload_owner(cfg, payload)
                             or st.get("host_char_owner") or "")
    st["last_tool_write"] = time.time()      # 记下"这次是工具写的"，下次别再拦自己
    save_state(st)
    return {
        "ok": True,
        "name": name,
        "how": how,
        "summary": char_summary(payload),
        "notes": notes,
        "payload": payload,
    }


def cmd_chars(args) -> None:
    cfg = load_cfg()
    auto_map_from_logs(cfg)
    wd = world_dir(cfg)
    idx = load_chars_index(cfg)

    if getattr(args, "use", None):
        name = args.use
        if recently_written(wd):
            warn("存档 45 秒内还在被写入 —— 游戏是不是还开着？")
            info("换角色必须在游戏退出状态下做，否则游戏保存时会把它盖回去")
            if not ask_yes("   仍然继续？", False):
                die("已取消")
            allow = True
        else:
            allow = False
        res = switch_character(cfg, name, force=True, allow_running=allow)
        ok(f"已把「{name}」的角色{res['how']}进 level.dat —— {res['summary']}")
        for note in res["notes"]:
            info(note)
        if getattr(args, "playerdata", False):
            pd = wd / "playerdata"
            pd.mkdir(exist_ok=True)
            target = pd / f"{offline_uuid(name)}.dat"
            write_player_dat(target, res["payload"])
            info(f"同时写成 playerdata/{target.name}（当访客时也用它）")
        return

    if getattr(args, "adopt_guest", False):
        src = wd / "playerdata" / f"{offline_uuid(cfg['player'])}.dat"
        payload = read_player_dat(src) if src.exists() else None
        if payload is None:
            die("找不到你的访客版角色（playerdata 里没有你 UUID 的那个文件）")
        save_char(cfg, cfg["player"], payload)
        ok(f"已用访客版覆盖你的房主版记录 —— {char_summary(payload)}")
        info(f"下次开局会自动套上这一份；想让当前存档立刻用上：chars --use {cfg['player']}")
        return

    if getattr(args, "forget", None):
        name = args.forget
        gone = []
        f = char_file(cfg, name)
        if f.exists():
            f.unlink()
            gone.append(f"chars/{f.name}")
        for k in ("stats", "advancements"):
            a = char_aux_file(cfg, name, k)
            if a.is_file():
                a.unlink()
                gone.append(f"chars/aux/{a.name}")
        gb = chars_dir(cfg) / "aux" / f"{name}.guest-backup.dat"
        if gb.is_file():
            gb.unlink()
            gone.append(f"chars/aux/{gb.name}")
        if name in idx.get("players", {}):
            idx["players"].pop(name, None)
            gone.append("chars/index.json 里的记录")
        if idx.get("owner") == name:
            idx["owner"] = None
        save_chars_index(cfg, idx)
        d = load_players(cfg)
        if name in d["players"]:
            d["players"].pop(name, None)
            save_players(cfg, d)
            gone.append("名册 players.json")
        if gone:
            ok(f"已彻底删掉「{name}」：{'、'.join(gone)}")
        else:
            warn(f"「{name}」本来就不存在，什么都没删")
        info("玩家自己的存档文件（playerdata/）没动，需要的话在「更多」里单独处理")
        return

    if getattr(args, "set_owner", None):
        name = args.set_owner
        st = load_state()
        st["host_char_owner"] = name
        save_state(st)
        uid = detect_local_uuid(wd, remember=False)
        if uid:
            add_alias(cfg, name, uid, "手动指定的房主")
        mark_char_world(cfg, name, wd)
        ok(f"已把「存档里的房主」记成 {name}")
        info("这只影响显示/记录，不会改任何存档内容")
        return

    if getattr(args, "as_name", None):
        name = args.as_name
        if not (wd / "level.dat").exists():
            die(f"本地还没有存档：{wd}")
        payload = get_host_char_payload(wd)
        if payload is None:
            die("这个存档的 level.dat 里没有 Player（服务端存档或新版存档格式），没东西可存")
        f = save_char(cfg, name, payload)
        aux = save_char_aux(cfg, name, wd)
        st = load_state()
        st["host_char_owner"] = name
        save_state(st)
        ok(f"已把当前房主角色存成 chars/{f.name} —— {char_summary(payload)}")
        if aux:
            info(f"成就/统计一起存了：{'、'.join(aux)}")
        info("它会跟着存档一起进仓库；换房主前跑 chars --use 自己的名字即可")
        return

    say("== 角色记录 ==")
    cur = get_host_char_payload(wd) if (wd / "level.dat").exists() else None
    if cur is not None:
        summary = char_summary(cur)
        info(f"当前存档里的房主角色：{summary}")
        # 认人顺序：① 身体指纹（等级+背包）认得出来 → 就是他
        #            ② chars/某人.dat 字节级一模一样
        #            ③ 记录里的 owner 且坐标/等级对得上
        #            ④ 上次记下的 host_char_owner（只当兜底）
        owner = identify_payload_owner(cfg, cur) or ""
        if not owner:
            for p in chars_dir(cfg).glob("*.dat"):
                try:
                    if nbt_unwrap(p.read_bytes()) == cur:
                        owner = p.stem
                        break
                except Exception:
                    continue
        if not owner:
            cand0 = idx.get("owner")
            if cand0 and idx.get("players", {}).get(cand0, {}).get("summary") == summary:
                owner = cand0
        if owner:
            info(f"   看起来是「{owner}」的")
        else:
            warn("   这份角色还没人认领（chars --owner 名字 可以指定）")
    elif wd.exists():
        info("当前存档的 level.dat 里没有 Player（服务端存档，或已是新版存档格式）")

    players = idx.get("players", {})
    say()
    stale = [n for n in players if not char_file(cfg, n).exists()]
    live = {n: e for n, e in players.items() if n not in stale}
    if live:
        say("已记录的角色：")
        for name, e in live.items():
            aux = [k for k in ("stats", "advancements") if char_aux_file(cfg, name, k).is_file()]
            mark = f"  成就: {'/'.join(aux)}" if aux else "  成就: 未存"
            info(f"{name:16} {e.get('summary', ''):34} {e.get('saved', '')}{mark}")
    else:
        warn("还没有记录任何角色（玩完 push 会自动记，或用 chars --as 名字 手动记）")
    if stale:
        info(f"（名册里还有 {'、'.join(stale)}，但身体文件已经没了，忽略）")
        info(f"   想彻底清掉：python mcmesh.py chars --forget {stale[0]}")

    pd = wd / "playerdata"
    if pd.is_dir():
        say()
        say("存档里的访客角色（playerdata/）：")
        names = uuid_name_map(cfg)
        host_payload = get_host_char_payload(wd) if (wd / "level.dat").exists() else None
        # chars/ 里各具身体，用来"按内容认人"（UUID 是哈希，没法反推名字）
        known_bodies = {}
        for p in chars_dir(cfg).glob("*.dat"):
            try:
                known_bodies[nbt_unwrap(p.read_bytes())] = p.stem
            except Exception:
                pass
        unregistered = []
        for f in sorted(pd.glob("*.dat")):
            payload = read_player_dat(f)
            label = names.get(f.stem, "")
            if host_payload is not None and payload == host_payload:
                # 内容和 level.dat 一模一样 → 这就是房主身份在本机的另一份拷贝，
                # 不管 UUID 挂在谁名下都别当成"另一个人的角色"
                who = names.get(f.stem, "")
                label = f"{who}（房主副本）" if who else "（房主自己的副本）"
            elif not label and payload is not None and payload in known_bodies:
                label = f"（= chars/{known_bodies[payload]}）"
            elif not label:
                label = "（未识别）"
                unregistered.append(f.stem)
            extra = f"   {char_summary(payload)}" if payload is not None else ""
            info(f"{label:16} {f.stem}{extra}")
        if unregistered:
            say()
            info("这几个 UUID 认不出来，是因为：")
            info("  · 名字 → UUID 是单向哈希（MD5），工具没法从 UUID 反推名字")
            info("  · 或者他是用正版账号进来的，UUID 是随机那种，永远算不出来")
            info("登记办法：")
            info("  python mcmesh.py players --add 名字                     （离线号：名字就能算出 UUID）")
            info("  python mcmesh.py players --add 名字 --uuid <那串UUID>    （正版号：直接指定）")
        if cfg["player"]:
            mine_guest = pd / f"{offline_uuid(cfg['player'])}.dat"
            if mine_guest.exists():
                payload = read_player_dat(mine_guest)
                if payload is not None:
                    say()
                    info(f"你的访客版角色：{char_summary(payload)}")
                    hostp = load_char(cfg, cfg["player"])
                    if hostp is not None and hostp != payload:
                        warn("和你的房主版不一样 —— 当房主 / 当访客各走一套")
                        info(f"   房主版：{char_summary(hostp)}")
                        info("   想改成以访客版为准：python mcmesh.py chars --adopt-guest")
    say()
    info("用法：chars --as 你的名字（记下当前房主角色） / chars --use 你的名字（换房主前套上自己）")


# ────────────────────────────────────────────────── 命令：players

def scan_logs_for_names(cfg, max_files: int = 16) -> dict:
    """从游戏日志里挖玩家名字 —— 存档本身不存名字，日志里才有。

    · `Setting user: xxx`          → 这台机器的账号名（房主是谁）
    · `UUID of player X is Y`      → 名字↔UUID（联机时游戏自己打的，最可靠）
    """
    logs = instance_dir(cfg) / "logs"
    users: set[str] = set()
    joined: set[str] = set()
    latest_user = ""
    pairs: dict[str, str] = {}
    if not logs.is_dir():
        return {"users": [], "pairs": {}}
    files = sorted(logs.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:max_files]:
        try:
            data = gzip.open(f, "rb").read() if f.suffix == ".gz" else f.read_bytes()
            text = data.decode("utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"Setting user: (\S+)", text):
            users.add(m.group(1))
            if not latest_user:
                latest_user = m.group(1)      # 最新的日志优先
        for m in re.finditer(r"UUID of player (\S+) is ([0-9a-fA-F-]{36})", text):
            pairs[m.group(2).lower()] = m.group(1)
        for m in re.finditer(r"\]: (\S+)(?:\[/[^\]]+\])? (?:joined the game|加入了游戏)", text):
            joined.add(m.group(1))
    return {"users": sorted(users), "pairs": pairs,
            "joined": sorted(joined), "latest_user": latest_user}


_LOG_MAP: dict = {"t": 0.0, "added": []}


def auto_map_from_logs(cfg, force: bool = False) -> list[str]:
    """自动从游戏日志把 名字<->UUID 对应起来（60 秒缓存，避免反复扫日志）。

    · `Setting user: X`       → 这台机器的账号名，其 UUID（客户端那个）归到 X 名下
    · `UUID of player X is Y` → 直接采信游戏给的对照
    · `X joined the game`     → 按离线规则算 UUID，和存档里的 playerdata 自动对上
    """
    if not force and time.time() - _LOG_MAP["t"] < 60:
        return list(_LOG_MAP["added"])
    _LOG_MAP["t"] = time.time()
    added: list[str] = []
    try:
        found = scan_logs_for_names(cfg)
    except Exception:
        return []
    known = {str(v.get("uuid", "")).lower() for v in load_players(cfg)["players"].values()}

    def add_name(name: str, uid: str = "", why: str = "从日志识别") -> None:
        if not name:
            return
        u = (uid or offline_uuid(name)).lower()
        if u not in known or name not in load_players(cfg)["players"]:
            register_player(cfg, name, "member", why, uid=u)
            known.add(u)
            added.append(name)

    for uid, name in (found.get("pairs") or {}).items():
        add_name(name, uid, "从日志识别（游戏给的 UUID）")
    for name in (found.get("joined") or []):
        add_name(name, "", "从日志识别（加入过游戏）")
    # 注意：`Setting user:` 只说明"谁启动过游戏"，不代表他进过世界，
    # 所以不能凭它建名册（会把从没进过游戏的账号也记进去）。

    # 本机客户端 UUID（00000000-…）归到"最新登录的账号名"名下
    latest = found.get("latest_user") or ""
    local = detect_local_uuid(world_dir(cfg), remember=False)
    if latest and local:
        if latest in load_players(cfg)["players"]:
            add_alias(cfg, latest, local, "本机客户端UUID")
    try:
        _LOG_MAP["added"] = added
    except Exception:
        pass
    return added


def cmd_names(args) -> None:
    cfg = load_cfg()
    found = scan_logs_for_names(cfg)
    users, pairs = found["users"], found["pairs"]
    say("== 从游戏日志里找到的名字 ==")
    if users:
        info("这台机器登录用过的账号名（Setting user）：")
        for u in users:
            info(f"   {u}   （离线 UUID：{offline_uuid(u)}）")
    else:
        info("（没找到 Setting user 行）")
    if pairs:
        say()
        info("名字 ↔ UUID（游戏自己记的）：")
        for uid, name in pairs.items():
            okk = (offline_uuid(name) == uid)
            info(f"   {name:14} {uid}   {'（与离线推导一致）' if okk else '（正版 UUID）'}")
    else:
        info("（没找到 UUID of player 行 —— 这条只在有人联机加入时才会打）")
    if not users and not pairs:
        warn("日志里什么都没有，可能日志被轮转掉了")
        return
    if getattr(args, "apply", False):
        added = []
        for name in users:
            register_player(cfg, name, "member", "从日志识别（本机账号）")
            added.append(name)
        for uid, name in pairs.items():
            register_player(cfg, name, "member", "从日志识别", uid=uid)
            added.append(name)
        ok("已登记：" + "、".join(sorted(set(added))))
        info("想把它设成自己的身份：python mcmesh.py init --player 名字 ...")
    else:
        say()
        info("想把它们登记进名册：python mcmesh.py names --apply")


def cmd_players(args) -> None:
    cfg = load_cfg()
    if getattr(args, "add", None):
        role = "op" if args.op else "member"
        uid = register_player(cfg, args.add, role, getattr(args, "note", "") or "",
                              getattr(args, "uuid", "") or "")
        ok(f"已登记 {args.add}（{role}）UUID {uid}")
        expect = offline_uuid(args.add)
        if uid != expect:
            info(f"（注意：这个名字按离线规则算出来是 {expect}，你手动指定成了 {uid}）")
        if cfg["mode"] == "server":
            write_server_identity_files(cfg)
            info("白名单已更新（下次开服生效）")
        return
    data = load_players(cfg)
    say("== 成员 ==")
    if not data["players"]:
        info("（还没有人）")
    for name, e in sorted(data["players"].items()):
        info(f"{name:16} {e.get('role', 'member'):7} {e.get('uuid') or offline_uuid(name)}  {e.get('note', '')}")
        for a in e.get("aliases") or []:
            info(f"{'':16} {'':7} {a}  （别名，同一个人）")
    say()
    info("添加：python mcmesh.py players --add 名字 [--op]")
    info("     python mcmesh.py players --add 名字 --uuid <UUID>   （正版账号的随机 UUID）")


# ────────────────────────────────────────────────── 菜单

def chars_interactive(cfg) -> None:
    """菜单里的角色移植：先列出，再让你选要干什么。"""
    cmd_chars(argparse.Namespace(as_name=None, use=None, playerdata=False, adopt_guest=False))
    names = sorted(p.stem for p in chars_dir(cfg).glob("*.dat"))
    say()
    say("  1) 换成某个人的角色（进游戏就用他的背包 / 坐标 / 等级）")
    say("  2) 把当前存档里的角色记成某个名字")
    say("  3) 用我的「访客版」覆盖我的「房主版」")
    say("  0) 返回主菜单")
    sel = ask("选一个", "0")
    if sel == "1":
        if not names:
            warn("还没有任何角色记录 —— 先玩一局并上传，或用 2 手动记录")
            return
        say("   可选的角色：")
        for i, n in enumerate(names, 1):
            say(f"     {i}) {n}")
        pick = ask("   换成谁（填编号或名字）", "1")
        target = names[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(names) else pick
        say()
        info("换角色前请确认游戏已经完全退出（否则游戏保存时会把角色盖回去）")
        cmd_chars(argparse.Namespace(as_name=None, use=target, playerdata=False, adopt_guest=False))
        info(f"进游戏后你就是「{target}」那份角色了")
    elif sel == "2":
        name = ask("   记成什么名字", cfg.get("player") or "")
        cmd_chars(argparse.Namespace(as_name=name, use=None, playerdata=False, adopt_guest=False))
    elif sel == "3":
        cmd_chars(argparse.Namespace(as_name=None, use=None, playerdata=False, adopt_guest=True))


def _menu_header(cfg) -> None:
    say()
    say("=" * 48)
    say("  MC 联机助手")
    if cfg:
        mode = "局域网" if cfg["mode"] == "lan" else "专用服"
        say(f"  我：{cfg['player']}    存档：{cfg['world']}    {mode}")
    say("=" * 48)


def chars_switch_simple(cfg) -> None:
    """一步换角色：列出编号，选一个就完事。"""
    wd = world_dir(cfg)
    names = sorted(p.stem for p in chars_dir(cfg).glob("*.dat"))
    cur = get_host_char_payload(wd) if (wd / "level.dat").exists() else None
    say()
    say("现在用的角色：" + (char_summary(cur) if cur else "（还没有存档）"))
    if not names:
        warn("还没有存过任何角色。先玩一局再按 2 上传，角色会自动记住。")
        return
    say()
    for i, n in enumerate(names, 1):
        payload = load_char(cfg, n)
        say(f"   {i}) {n:<14} {char_summary(payload) if payload else '（读不出来）'}")
    say("   0) 取消")
    pick = (ask("换成哪个", "0") or "0").strip()
    if not pick.isdigit() or not 1 <= int(pick) <= len(names):
        return
    target = names[int(pick) - 1]
    payload = load_char(cfg, target)
    if cur is not None and payload == cur:
        info(f"现在就是「{target}」，不用换")
        return
    say()
    info("换之前请确认游戏已经完全退出")
    try:
        cmd_chars(argparse.Namespace(as_name=None, use=target, playerdata=False,
                                     adopt_guest=False))
    except SystemExit:
        return
    say()
    ok(f"好了 —— 现在进游戏就是「{target}」那份角色")


def allow_commands(world: Path):
    """单机存档的"允许作弊"开关（level.dat -> Data.allowCommands）。None = 没有这个字段。"""
    lvl = world / "level.dat"
    if not lvl.exists():
        return None
    try:
        raw = gzip.open(lvl, "rb").read()
    except Exception:
        return None
    ent = nbt_find(raw, ("Data", "allowCommands"))
    return raw[ent[3]] if ent else None


def set_allow_commands(world: Path, on: bool) -> str:
    """打开/关闭"允许作弊"——单机里这就是管理员权限的总开关。

    只改一个字节（0↔1），其它字段一个都不动；改前备份、改后校验，不一致自动回滚。
    """
    lvl = world / "level.dat"
    if not lvl.exists():
        die(f"找不到 {lvl}")
    raw = gzip.open(lvl, "rb").read()
    want = 1 if on else 0
    ent = nbt_find(raw, ("Data", "allowCommands"))
    if ent is not None:
        if raw[ent[3]] == want:
            return "没变"
        new = raw[:ent[3]] + bytes([want]) + raw[ent[4]:]
    else:
        data_ent = nbt_find(raw, ("Data",))
        if data_ent is None:
            die("level.dat 结构异常：找不到 Data 标签")
        end_pos = data_ent[4] - 1                      # Data 复合的 END(0)
        block = b"\x01" + struct.pack(">H", len("allowCommands")) + b"allowCommands" + bytes([want])
        new = raw[:end_pos] + block + raw[end_pos:]

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / f"level.dat-{datetime.now():%Y%m%d-%H%M%S}.bak"
    shutil.copy2(lvl, bak)
    tmp = lvl.with_name("level.dat.mcmesh-tmp")
    try:
        with gzip.open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, lvl)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"写不进 level.dat（游戏还开着？）：{e}\n"
            "→ 请在 PCL 里完全退出游戏再点一次") from e
    if allow_commands(world) != want:
        shutil.copy2(bak, lvl)
        die(f"写入后校验不一致，已自动回滚（备份：{bak}）")
    return "已打开" if on else "已关闭"


def cmd_op(args) -> None:
    cfg = load_cfg()
    wd = world_dir(cfg)
    if not (wd / "level.dat").exists():
        die(f"本地还没有存档：{wd}")
    off = bool(getattr(args, "off", False))
    cur = allow_commands(wd)
    say("== 管理员权限（单机存档的「允许作弊」）==")
    info(f"存档：{wd.name}")
    info(f"现在的状态：{'打开（/gamemode /give /tp 都能用）' if cur == 1 else '关闭（命令全都用不了）'}")
    if java_processes():
        warn("检测到游戏还在运行")
        die("先把游戏完全退出，再改这个开关 —— level.dat 现在被占着")
    if cur == (0 if off else 1) and not getattr(args, "force", False):
        ok("已经是这个状态，不用改")
        info("想关掉：python mcmesh.py op --off" if not off else "想打开：python mcmesh.py op")
        return
    how = set_allow_commands(wd, not off)
    if not off:
        ok(f"「允许作弊」{how} —— 进游戏就能用管理员命令了")
        info("要给自己 op：进游戏按 T，输入 /op 你的名字；想开创造：/gamemode creative")
    else:
        ok(f"「允许作弊」{how}")
    info("只改了「允许作弊」这一个开关，不动难度、模式、角色数据")


def run_fix(cfg, world: Path = None) -> dict:
    """成就绑定 + 打开「允许作弊」。给命令行和窗口共用，不打印大段文字。

    成就走 bind_aux：把这个角色名下**所有** UUID 的统计/成就合并成一份并集，
    再写回所有 UUID 文件 + 档案 —— 也就是"把角色相关数据全绑到身上"。
    """
    wd = world or world_dir(cfg)
    host = get_host_char_payload(wd)
    if host is None:
        raise ValueError("这个存档的 level.dat 里没有玩家角色（服务端存档？）")
    owner = (identify_payload_owner(cfg, host)
             or load_state().get("host_char_owner") or cfg.get("player", ""))
    res = {"owner": owner, "adv": -1, "op": "", "uuid": body_uuid(host), "ids": []}
    if owner:
        save_char(cfg, owner, host)          # 顺手把当前身体归档
        bound = bind_aux(cfg, owner, wd)     # 合并并集 + 写所有身份文件 + 档案
        st = load_state()
        st["host_char_owner"] = owner
        save_state(st)
        live = live_player_uuid(cfg, wd)
        res["uuid"] = live or res["uuid"]
        res["ids"] = bound.get("uuids") or []
        res["adv"] = bound.get("advancements", -1)
        if res["adv"] < 0:
            f = wd / "advancements" / f"{live}.json"
            if f.is_file():
                res["adv"] = _aux_done(f)
    res["op"] = "已经是打开的" if allow_commands(wd) == 1 else set_allow_commands(wd, True)
    return res


def aux_status(cfg, world: Path = None) -> dict:
    """当前状态：谁是房主、并集里有多少成就、游戏那份有多少、作弊开关开没开。"""
    wd = world or world_dir(cfg)
    host = get_host_char_payload(wd)
    out = {"owner": "", "live": "", "live_adv": -1, "merged_adv": -1, "op": allow_commands(wd)}
    if host is None:
        return out
    owner = (identify_payload_owner(cfg, host)
             or load_state().get("host_char_owner") or cfg.get("player", ""))
    out["owner"] = owner
    out["live"] = body_uuid(host)
    f = wd / "advancements" / f"{out['live']}.json"
    if f.is_file():
        out["live_adv"] = _aux_done(f)
    if owner:
        srcs = collect_aux(cfg, owner, wd, "advancements")
        if srcs:
            out["merged_adv"] = _aux_done_dict(merge_advancements(srcs))
    return out


def wait_for_game_exit(timeout: float = 7200.0, poll: float = 3.0) -> bool:
    """等 Minecraft 进程结束。返回 True = 已经没在跑了（或超时）。"""
    t0 = time.time()
    announced = False
    while True:
        procs = java_processes()
        if not procs:
            return True
        if not announced:
            info(f"检测到游戏还在运行（{'、'.join(procs)}）")
            info("请在游戏里按「保存并退出 → 退出游戏」，或者直接结束游戏进程")
            info("游戏完全退出后这里会自动继续（别关这个黑窗口）")
            announced = True
        if time.time() - t0 > timeout:
            warn("等太久了，先放弃（想再试就重新运行）")
            return False
        time.sleep(poll)


def cmd_fix(args) -> None:
    """一键：把成就/统计绑到"游戏真正在用的那个 UUID"，并把「允许作弊」打开。

    典型场景：进游戏发现成就列表是空的、或者没有管理员权限（op）。
    """
    cfg = load_cfg()
    wd = world_dir(cfg)
    if not (wd / "level.dat").exists():
        die(f"本地还没有存档：{wd}")
    say("== 一键修复：成就绑定 + 管理员权限 ==")
    if java_processes():
        warn("游戏现在开着 —— 现在改，等游戏一存档/退出就会把它内存里的旧状态写回去")
        info("【马上就能用、不用退出游戏的办法】")
        info("   1) ESC → 对局域网开放 → 允许作弊：开 → 创建局域网世界（你就是 op）")
        info("   2) 按 T 输入：/advancement grant @s everything  （直接把成就全给了）")
        info("")
        if getattr(args, "wait", False):
            info("正在等游戏退出…退出后会自动执行修复")
            if not wait_for_game_exit():
                die("没等到游戏退出，什么都没改")
        else:
            info("或者让它在后台等：python mcmesh.py fix --wait（游戏退出后自动修）")
            if not getattr(args, "force", False):
                die("已停下，什么都没改")
            warn("（--force：照样改，但退出游戏前先别让游戏存档）")
        # 游戏刚退出，等它把最后的存档写完
        time.sleep(3)
    host = get_host_char_payload(wd)
    if host is not None:
        info(f"存档里的身体：{char_summary(host)}")
        info(f"游戏在用的 UUID：{body_uuid(host) or '（读不出来）'}")
    try:
        r = run_fix(cfg, wd)
    except ValueError as e:
        die(str(e))
    if r["owner"]:
        ok(f"成就/统计已绑到「{r['owner']}」：{r['adv']} 条已完成")
    else:
        warn("这具身体认不出是谁的；先用 chars --as 名字 标一下，再跑 fix")
    ok(f"「允许作弊」（管理员权限）{r['op']} —— 进游戏就能用 /gamemode、/give、/tp")
    info("要 op 自己：进游戏按 T 输入 /op 你的名字")


def cmd_me(args) -> None:
    """看 / 改"我的默认 ID"（config.player）—— 换账号、把工具给朋友时最容易搞错的东西。"""
    cfg = load_cfg()
    cur = cfg.get("player", "")
    new = (getattr(args, "name", None) or "").strip()
    say("== 我的身份（默认 ID）==")
    info(f"现在：{cur or '（还没设）'}")
    if cur:
        info(f"名字算出的离线 UUID：{offline_uuid(cur)}")
        names = uuid_name_map(cfg)
        mine = sorted({u for u, n in names.items() if n == cur})
        if mine:
            info("挂在这个名字下的 UUID（成就/统计就按这些存）：")
            for u in mine:
                info(f"   {u}")
        f = char_file(cfg, cur)
        info(f"角色档案：{'chars/' + f.name if f.exists() else '（还没有，玩完上传就会生成）'}")
    say()
    roster = sorted((load_players(cfg).get("players") or {}).keys())
    if roster:
        info("名册里已有的人：" + "、".join(roster))

    if not new:
        info("")
        info("改名字：python mcmesh.py me 你的ID     （界面右上角点「我的 ID」也行）")
        info("注意：改名 = 换离线 UUID = 游戏里是另一个角色；")
        info("      原来那具身体还在旧名字下，用「以选中身份进入游戏」选它就行。")
        return

    if new == cur:
        ok("没有变化，还是这个名字")
        return
    if any(ch.isspace() for ch in new) or len(new) > 16:
        die("名字里不能有空格，长度也别超过 16 个字符（要和游戏 ID 一致）")
    old_entry = ((load_players(cfg).get("players") or {}).get(cur) or {})
    old_role = old_entry.get("role") or "owner"
    cfg["player"] = new
    save_cfg(cfg)
    register_player(cfg, new, old_role, (f"本机（原 {cur}）" if cur else "本机"))
    ok(f"默认 ID 已改成「{new}」")
    info(f"新的离线 UUID：{offline_uuid(new)}")
    if cur:
        info(f"旧名字「{cur}」的角色档案没动（还在 chars/ 和名册里）")
        info(f"   想接着玩旧身体：在列表里选「{cur}」那张卡")
        info(f"   想把旧身体改到新名字下：python mcmesh.py chars --as {new}")
    info("之后「以选中身份进入游戏」、上传，用的都是新名字这个身份")


def cmd_auto(args) -> None:
    """挂机修复：游戏运行时不动手；一检测到游戏退出，立刻把成就/权限修好。

    这样你随便用 PCL 启动、退出，都不用记得先点工具——它一直盯着。
    """
    cfg = load_cfg()
    wd = world_dir(cfg)
    if not (wd / "level.dat").exists():
        die(f"本地还没有存档：{wd}")
    say("== 自动修复（挂着就行，Ctrl+C 结束） ==")
    info(f"存档：{wd.name}")
    info("游戏运行时我不会动文件；你退出游戏后，我立刻把成就/权限修好。")
    info("修好了会在这里打一行；再进游戏就是对的。")
    last = None
    while True:
        running = java_processes()
        if running:
            if last != "run":
                info(f"游戏在运行（{'、'.join(running)}）—— 等它退出")
                last = "run"
            time.sleep(5)
            continue
        try:
            st = aux_status(cfg, wd)
            need = (st["op"] != 1) or (st["merged_adv"] > st["live_adv"] >= 0) \
                   or (st["live_adv"] < 0 and st["merged_adv"] > 0)
            stamp = f"{st['owner']}|{st['merged_adv']}|{st['op']}"
            if need or stamp != last:
                time.sleep(2)                    # 等游戏把最后的存档写完
                r = run_fix(cfg, wd)
                msg = (f"已修好：成就 {r['adv']} 条（{r['owner']}）"
                       f" / 允许作弊 {r['op']}")
                ok(msg)
                info("现在可以进游戏了（进了就别再让我重复写，我会自动判断）")
                last = f"{r['owner']}|{r['adv']}|1"
            else:
                last = stamp
        except SystemExit:
            pass
        except Exception as e:
            warn(f"这次没修成：{e}")
            last = None
        time.sleep(5)


def menu_advanced(cfg) -> None:
    while True:
        say()
        say("======== 更多功能 ========")
        say("  1) 导出到 U 盘 / 文件夹（拷给别人）")
        say("  2) 从 U 盘 / 文件夹导入（别人给你的）")
        say("  3) 从旧存档导入玩家数据")
        say("  4) 生成 / 修复服务端（专用服模式）")
        say("  5) 成员管理")
        say("  6) 体检（路径/环境有问题时用）")
        say("  7) 打开/关闭「允许作弊」（管理员权限）")
        say("  8) 修复成就绑定 + 打开作弊（一键）")
        say("  9) 改「我的默认 ID」（换账号 / 给朋友用之前改）")
        say(" 10) 重新配置")
        say("  0) 返回")
        sel = (ask("选一个", "0") or "0").strip()
        if sel == "0":
            return
        try:
            if sel == "1":
                cmd_push(argparse.Namespace(message=None, force=False, to_dir=""))
            elif sel == "2":
                cmd_pull(argparse.Namespace(force=False, from_dir=""))
            elif sel == "3":
                cmd_import_players(argparse.Namespace(
                    from_world=ask("旧世界文件夹路径"), force=False))
            elif sel == "4":
                cmd_serverpack(argparse.Namespace(force=False, dry_run=False))
            elif sel == "5":
                cmd_players(argparse.Namespace(add=None, op=False, note="", uuid=None))
            elif sel == "6":
                cmd_doctor(argparse.Namespace())
            elif sel == "7":
                cur = allow_commands(world_dir(cfg))
                dest = ask(
                    f"「允许作弊」现在是{'打开' if cur == 1 else '关闭'}，要改成"
                    f"{'关闭' if cur == 1 else '打开'}吗", "y")
                if dest.lower() in ("y", "yes", "是"):
                    cmd_op(argparse.Namespace(off=(cur == 1), force=True))
            elif sel == "8":
                cmd_fix(argparse.Namespace(force=False, wait=True))
            elif sel == "9":
                new = ask(f"现在的默认 ID 是 {cfg.get('player')}，改成什么（直接回车=不改）", "")
                if new.strip():
                    cmd_me(argparse.Namespace(name=new.strip()))
            elif sel == "10":
                cmd_init(argparse.Namespace(
                    instance="", player=None, repo=None, branch=None, mode=None,
                    world=None, memory=None, launcher=None, no_launcher=False))
                return
            else:
                warn("没有这个选项")
        except SystemExit:
            pass


def menu() -> None:
    cfg = load_cfg(required=False)
    if cfg is None:
        say("第一次用，先花 1 分钟配置一下（后面都不用再填了）")
        try:
            cmd_init(argparse.Namespace(
                instance="", player=None, repo=None, branch=None, mode=None,
                world=None, memory=None, launcher=None, no_launcher=False))
        except SystemExit:
            return
        cfg = load_cfg(required=False)
        if cfg is None:
            return

    while True:
        _menu_header(cfg)
        say("  1) 开始玩（拉最新存档 → 开游戏）")
        say("  2) 玩完了，上传存档")
        say("  3) 看状态")
        say("  4) 换角色")
        say("  5) 更多功能…")
        say("  0) 退出")
        sel = (ask("选一个（直接回车 = 1）", "1") or "1").strip().upper()
        if sel == "0":
            return
        try:
            if sel == "1":
                cmd_host(argparse.Namespace())
            elif sel == "2":
                cmd_push(argparse.Namespace(message=None, force=False, to_dir=None))
            elif sel == "3":
                cmd_status(argparse.Namespace())
            elif sel == "4":
                chars_switch_simple(cfg)
            elif sel == "5":
                menu_advanced(cfg)
            else:
                warn("没有这个选项，输入 0-5")
        except SystemExit:
            pass
        cfg = load_cfg(required=False)


# ────────────────────────────────────────────────── 入口

def main() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                # 控制台直接写 Unicode，不用改编码；被管道接走时统一用 UTF-8
                if not stream.isatty():
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            if not sys.stdin.isatty():
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="mcmesh", description="我的世界联机存档同步助手")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="首次配置")
    s.add_argument("--instance", default="")
    s.add_argument("--player", default=None, help="你游戏里的 ID")
    s.add_argument("--repo", default=None, help="GitHub 仓库地址")
    s.add_argument("--branch", default=None)
    s.add_argument("--mode", default=None, help="lan 或 server")
    s.add_argument("--world", default=None, help="存档文件夹名")
    s.add_argument("--memory", default=None, help="专用服内存上限，如 8G")
    s.add_argument("--launcher", default=None, help="启动器 exe 路径")
    s.add_argument("--no-launcher", dest="no_launcher", action="store_true")
    sub.add_parser("status", help="查看状态")
    sub.add_parser("doctor", help="体检")
    s = sub.add_parser("pull", help="拉取最新存档")
    s.add_argument("--force", action="store_true", help="即使本地有未上传进度也覆盖")
    s.add_argument("--from", dest="from_dir", nargs="?", const="", default=None,
                   help="从 U 盘/文件夹导入（不给路径就让你选盘）")
    s = sub.add_parser("push", help="上传存档")
    s.add_argument("-m", "--message", default=None)
    s.add_argument("--force", action="store_true", help="以本机存档为准，覆盖远端")
    s.add_argument("--to", dest="to_dir", nargs="?", const="", default=None,
                   help="导出到 U 盘/文件夹（不给路径就让你选盘）")
    sub.add_parser("backup", help="手动备份本地存档")
    s = sub.add_parser("play", help="拉存档 + 打开游戏")
    s.add_argument("--force", action="store_true")
    sub.add_parser("host", help="开局（结束时自动上传）")
    s = sub.add_parser("serverpack", help="从整合包生成 Fabric 服务端")
    s.add_argument("--dry-run", action="store_true", help="只打印要保留/排除哪些 mod")
    s.add_argument("--force", action="store_true", help="重新下载服务端 jar")
    s = sub.add_parser("names", help="从游戏日志里识别玩家名字")
    s.add_argument("--apply", action="store_true", help="把识别到的名字登记进名册")
    s = sub.add_parser("players", help="成员管理")
    s.add_argument("--add", default=None)
    s.add_argument("--op", action="store_true")
    s.add_argument("--note", default=None)
    s.add_argument("--uuid", default=None, help="手动指定 UUID（正版账号用）")
    s = sub.add_parser("chars", help="角色移植：换房主时带上自己的背包")
    s.add_argument("--as", dest="as_name", default=None, help="把当前存档里的房主角色记成这个名字")
    s.add_argument("--owner", dest="set_owner", default=None,
                   help="直接指定存档里的房主是谁（只改记录，不改存档）")
    s.add_argument("--use", default=None, help="把某个已记录的角色写进当前存档")
    s.add_argument("--playerdata", action="store_true", help="同时写成 playerdata 文件")
    s.add_argument("--adopt-guest", dest="adopt_guest", action="store_true",
                   help="用你当访客时那份角色覆盖房主版记录")
    s.add_argument("--forget", default=None, help="彻底删掉某个角色的记录（身体+成就+名册）")
    s = sub.add_parser("op", help="打开/关闭存档的「允许作弊」（单机里的管理员权限）")
    s.add_argument("--off", action="store_true", help="关掉（默认是打开）")
    s.add_argument("--force", action="store_true", help="已经是这个状态也照样写一次")
    s = sub.add_parser("fix", help="一键修复：成就绑定到当前身份 + 打开管理员权限")
    s.add_argument("--force", action="store_true", help="游戏开着也照样改（不推荐）")
    s.add_argument("--wait", action="store_true",
                   help="游戏开着就等它退出，退出后自动修复（推荐）")
    sub.add_parser("auto", help="挂着：一退出游戏就自动修成就/权限（推荐长期用）")
    s = sub.add_parser("me", help="看/改我的默认 ID（换账号、给朋友用之前先改这个）")
    s.add_argument("name", nargs="?", default=None, help="新的名字（不填就只看）")
    s = sub.add_parser("import-players", help="把别的世界里的玩家数据搬进当前世界")
    s.add_argument("--from", dest="from_world", default=None, help="来源世界文件夹")
    s.add_argument("--force", action="store_true", help="目标世界已存在同名玩家时也覆盖")

    args = ap.parse_args()
    if not args.cmd:
        menu()
        return
    {
        "init": cmd_init,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "pull": cmd_pull,
        "push": cmd_push,
        "backup": cmd_backup,
        "play": cmd_play,
        "host": cmd_host,
        "serverpack": cmd_serverpack,
        "players": cmd_players,
        "names": cmd_names,
        "chars": cmd_chars,
        "op": cmd_op,
        "fix": cmd_fix,
        "auto": cmd_auto,
        "me": cmd_me,
        "import-players": cmd_import_players,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
