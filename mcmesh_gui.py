#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MC 联机助手 · 桌面小窗口（卡片式深色界面）

流程：选存档 → 看这个存档里所有人的角色 → 选中一个身份 → 进入游戏
另外：上传存档 / 拉取存档 / 体检

逻辑全部复用 mcmesh.py。没有图形环境想自测：python mcmesh_gui.py --selftest
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parent)

import mcmesh as core  # noqa: E402

# ────────────────────────────────────────────────── 配色 / 字体
C = {
    "bg": "#15181d",
    "panel": "#1e232b",
    "card": "#252b35",
    "card_hover": "#2c333f",
    "card_sel": "#2a3a34",
    "border": "#333b47",
    "border_sel": "#4ade80",
    "text": "#e9edf2",
    "muted": "#8b95a5",
    "dim": "#5f6a7a",
    "accent": "#4ade80",
    "accent_hover": "#6ee795",
    "accent_text": "#0f1a13",
    "blue": "#5ba8ff",
    "blue_hover": "#7fbcff",
    "danger": "#ff6b6b",
    "log_bg": "#12151a",
    "log_text": "#9fb0c0",
}
F = {
    "title": ("Microsoft YaHei UI", 14, "bold"),
    "h2": ("Microsoft YaHei UI", 11, "bold"),
    "body": ("Microsoft YaHei UI", 10),
    "small": ("Microsoft YaHei UI", 9),
    "tiny": ("Microsoft YaHei UI", 8),
    "num": ("Microsoft YaHei UI", 15, "bold"),
    "mono": ("Consolas", 9),
}


# ────────────────────────────────────────────────── 数据层（与界面无关）

def collect_worlds(cfg) -> list[dict]:
    saves = core.instance_dir(cfg) / "saves"
    out = []
    if not saves.is_dir():
        return out
    for d in sorted(saves.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir() or not (d / "level.dat").exists():
            continue
        n, size = core.dir_stats(d)
        data = core.read_level_dat(d / "level.dat").get("Data", {})
        day = data.get("DayTime")
        pd = d / "playerdata"
        out.append({
            "name": d.name,
            "path": d,
            "day": (int(day) // 24000 + 1) if isinstance(day, (int, float)) and day else 0,
            "size": core.human_size(size),
            "files": n,
            "players": len(list(pd.glob("*.dat"))) if pd.is_dir() else 0,
            "regions": len(list((d / "region").glob("*.mca"))) if (d / "region").is_dir() else 0,
            "mtime": d.stat().st_mtime,
        })
    return out


def collect_characters(cfg, world: Path) -> list[dict]:
    """这个存档里能选的身份：chars 里记录的 + level.dat 里的 + playerdata 里的。

    **同一具身体只出现一次** —— 按"稳定指纹"（等级+背包+末影箱）合并，
    因为坐标/饥饿值/经验总值会变，按字节比对会把同一个人拆成好几张卡。
    """
    rows: dict[str, dict] = {}
    names = core.uuid_name_map(cfg)
    idx = core.load_chars_index(cfg)
    in_this_world = {nm for nm, e in (idx.get("players") or {}).items()
                     if world.name in (e.get("worlds") or [])}
    own = cfg.get("player", "")

    def fp_of(payload) -> str:
        fp = core.body_fingerprint(payload)
        if not fp:                       # 兜底：解析不出来就用字节本身当键
            import hashlib
            fp = "raw:" + hashlib.sha1(payload).hexdigest()
        return fp

    # 谁的"身体"更该被拿来切换：房主那份 > chars 档案 > playerdata 快照
    RANK = {"房主": 0, "记录": 1, "访客": 2}

    def add(payload, label, source, usable_name="", role="记录"):
        if payload is None:
            return
        key = fp_of(payload)
        row = rows.get(key)
        if row:
            if source not in row["sources"]:
                row["sources"].append(source)
            if usable_name and not row["usable"]:
                row["usable"] = usable_name
            if RANK.get(role, 3) < RANK.get(row["role"], 3):
                row["payload"] = payload          # 换成更权威的那份身体
                row["role"] = role
                row["ident"] = label
                row["summary"] = core.char_summary(payload)
            return
        rows[key] = {"payload": payload, "label": label, "ident": label,
                     "role": role, "sources": [source],
                     "summary": core.char_summary(payload), "usable": usable_name}

    chars_by_body: dict[bytes, str] = {}
    # 身体会动（坐标/饥饿值/经验总值都会变），所以按"稳定指纹"认人
    chars_by_fp: dict[str, str] = {}
    for p in sorted(core.chars_dir(cfg).glob("*.dat")):
        try:
            payload = core.nbt_unwrap(p.read_bytes())
        except Exception:
            continue
        aux = [k for k in ("stats", "advancements") if core.char_aux_file(cfg, p.stem, k).is_file()]
        chars_by_body[payload] = p.stem
        chars_by_fp.setdefault(core.body_fingerprint(payload), p.stem)
        add(payload, p.stem, "chars", usable_name=p.stem, role="记录")
        if aux:
            rows[fp_of(payload)]["ach"] = "已存"

    # level.dat 里现在装着的身体
    host = core.get_host_char_payload(world)
    local_uid = (core.detect_local_uuid(world, remember=False) or "").lower()
    if host is not None:
        # 认人顺序：① 按"稳定指纹"认（chars/ 档案 + playerdata 里认得出的名字）—— 最准
        #            ② 上次记下的房主（粘住，但只当兜底）
        #            ③ 本机客户端 UUID 对应的账号名（最弱）
        sticky = (core.load_state().get("host_char_owner") or "")
        if sticky and not core.char_file(cfg, sticky).exists() and sticky not in names.values():
            sticky = ""
        owner = (core.identify_payload_owner(cfg, host)
                 or chars_by_body.get(host)
                 or chars_by_fp.get(core.body_fingerprint(host))
                 or sticky or names.get(local_uid) or "")
        add(host, owner or "未知 ID", "level.dat", role="房主")
        rows[fp_of(host)]["uuid"] = local_uid

    pd = world / "playerdata"
    if pd.is_dir():
        for f in sorted(pd.glob("*.dat")):
            payload = core.read_player_dat(f)
            label = names.get(f.stem.lower()) or f.stem[:8] + "…"
            add(payload, label, "playerdata", role="访客")
            if payload is not None:
                rows[fp_of(payload)].setdefault("uuid", f.stem.lower())

    out = list(rows.values())
    for r in out:
        r.setdefault("ach", "未存")
        r["parts"] = {}
        for part in r["summary"].split(" · "):
            for key in ("坐标", "等级", "背包", "血量"):
                if part.startswith(key):
                    r["parts"][key] = part[len(key):].strip()
        usable = r.get("usable") or ""
        r["current"] = ("level.dat" in r["sources"])
        from_world = "level.dat" in r["sources"] or "playerdata" in r["sources"]
        r["in_world"] = bool(from_world or (usable and usable in in_this_world))
    out.sort(key=lambda r: (not r["in_world"], r["usable"] == "", r["label"]))
    return out


# ────────────────────────────────────────────────── 小控件

def _make_button(parent, text, command, kind="ghost", width=None, font=None):
    """扁平按钮：kind = primary / blue / ghost。"""
    import tkinter as tk
    bg, fg, hov = {
        "primary": (C["accent"], C["accent_text"], C["accent_hover"]),
        "blue": (C["blue"], "#08131f", C["blue_hover"]),
        "ghost": (C["panel"], C["text"], C["card_hover"]),
    }[kind]
    b = tk.Label(parent, text=text, bg=bg, fg=fg, font=font or F["body"],
                 padx=16, pady=8, cursor="hand2", anchor="center")
    if width:
        b.configure(width=width)

    def on_enter(_):
        if str(b["state"] if "state" in b.keys() else "") != "disabled":
            b.configure(bg=hov)

    def on_leave(_):
        b.configure(bg=b._bg if hasattr(b, "_bg") else bg)

    def on_click(_):
        if getattr(b, "_disabled", False):
            return
        command()

    b._bg = bg
    b._disabled = False
    b.bind("<Enter>", on_enter)
    b.bind("<Leave>", on_leave)
    b.bind("<Button-1>", on_click)

    def set_enabled(flag: bool):
        b._disabled = not flag
        b.configure(bg=bg if flag else C["card"], fg=fg if flag else C["dim"],
                    cursor="hand2" if flag else "arrow")

    b.set_enabled = set_enabled
    return b


def paint_children(widget, color) -> None:
    """把卡片内部"底色是卡片色"的控件统一换色；标签/彩签保持原色。"""
    for child in widget.winfo_children():
        try:
            if str(child.cget("bg")) in (C["card"], C["card_sel"]):
                child.configure(bg=color)
        except Exception:
            pass
        paint_children(child, color)


def _card(parent, pad=0, bg=None):
    import tkinter as tk
    f = tk.Frame(parent, bg=bg or C["card"], highlightthickness=1,
                 highlightbackground=C["border"], highlightcolor=C["border"])
    if pad:
        f.configure(padx=pad, pady=pad)
    return f


# ────────────────────────────────────────────────── 界面

def detect_scale() -> float:
    """缩放比。

    Windows 高 DPI 下有两种选择：
      · 声明 DPI 感知 → 字清晰，但所有像素尺寸都要自己乘缩放比（漏一处就糊/错位）
      · 不声明     → 由 Windows 整体缩放窗口位图，比例永远正确，代价是字略软
    这里选后者：界面元素多、且要照顾 100%~250% 各种缩放比，整体缩放更稳。
    """
    return 1.0


SC = detect_scale()


def px(v: float) -> int:
    """逻辑像素 → 实际像素。"""
    return int(round(v * SC))


def setup_wizard(parent) -> dict | None:
    """第一次用的配置表（exe 版没有黑框，只能在窗口里填）。

    返回配置好的 cfg；用户取消返回 None。
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from tkinter import ttk

    old = core.load_cfg(required=False) or dict(core.DEFAULT_CONFIG)
    win = tk.Toplevel(parent)
    win.title("第一次使用 · 填 6 项就好")
    win.configure(bg=C["bg"])
    win.geometry(f"{px(640)}x{px(430)}+{px(200)}+{px(140)}")
    win.transient(parent)

    tk.Label(win, text="先填这几项（以后都能改）", bg=C["bg"], fg=C["text"],
             font=F["title"]).pack(anchor="w", padx=18, pady=(14, 2))
    tk.Label(win, text="名字必须和你在游戏里的 ID 完全一致；定了别改（改了就是另一个角色）",
             bg=C["bg"], fg=C["muted"], font=F["small"]).pack(anchor="w", padx=18, pady=(0, 8))

    body = tk.Frame(win, bg=C["bg"])
    body.pack(fill="both", expand=True, padx=18)
    vars_ = {}

    def row(key: str, label: str, default: str = "", browse: str = "", width: int = 46):
        r = tk.Frame(body, bg=C["bg"])
        r.pack(fill="x", pady=4)
        tk.Label(r, text=label, bg=C["bg"], fg=C["muted"], font=F["small"],
                 width=12, anchor="w").pack(side="left")
        v = tk.StringVar(value=default)
        vars_[key] = v
        e = tk.Entry(r, textvariable=v, width=width, bg=C["card"], fg=C["text"],
                     insertbackground=C["text"], relief="flat", font=F["small"])
        e.pack(side="left", ipady=4)
        if browse:
            def pick():
                if browse == "dir":
                    p = filedialog.askdirectory(title="选目录")
                else:
                    p = filedialog.askopenfilename(title="选 exe",
                                                   filetypes=[("exe", "*.exe"), ("所有文件", "*.*")])
                if p:
                    v.set(p)
            _make_button(r, "浏览", pick, "ghost", font=F["small"]).pack(side="left", padx=6)
        return e

    e_name = row("player", "我的 ID", old.get("player", ""))
    row("instance", "整合包目录", old.get("instance", ""), browse="dir")
    row("world", "存档文件夹名", old.get("world", ""))
    row("launcher_exe", "启动器 exe", old.get("launcher_exe", ""), browse="exe")
    row("repo_url", "仓库地址", old.get("repo_url", ""))
    row("branch", "分支名", old.get("branch") or "main")

    mode_row = tk.Frame(body, bg=C["bg"])
    mode_row.pack(fill="x", pady=4)
    tk.Label(mode_row, text="联机方式", bg=C["bg"], fg=C["muted"], font=F["small"],
             width=12, anchor="w").pack(side="left")
    mode_var = tk.StringVar(value=old.get("mode") or "lan")
    for val, text in (("lan", "单人存档 + 局域网（推荐）"), ("server", "Fabric 专用服")):
        tk.Radiobutton(mode_row, text=text, value=val, variable=mode_var,
                       bg=C["bg"], fg=C["text"], selectcolor=C["card"],
                       activebackground=C["bg"], activeforeground=C["text"],
                       font=F["small"]).pack(side="left", padx=4)

    out = {"cfg": None}

    def save() -> None:
        name = (vars_["player"].get() or "").strip()
        inst = (vars_["instance"].get() or "").strip().strip('"')
        if not name:
            messagebox.showwarning("还差一步", "「我的 ID」必填 —— 就是你游戏里的名字。", parent=win)
            return
        if any(ch.isspace() for ch in name) or len(name) > 16:
            messagebox.showwarning("名字不对", "名字不能有空格，长度别超过 16 个字符。", parent=win)
            return
        if not inst or not Path(inst).is_dir():
            messagebox.showwarning("路径不对", "「整合包目录」要是存在的文件夹\n"
                                             "（里面有 mods、saves 的那一层）。", parent=win)
            return
        updates = {
            "player": name,
            "instance": inst,
            "world": (vars_["world"].get() or "").strip() or "新的世界",
            "launcher_exe": (vars_["launcher_exe"].get() or "").strip(),
            "repo_url": (vars_["repo_url"].get() or "").strip(),
            "branch": (vars_["branch"].get() or "main").strip() or "main",
            "mode": mode_var.get(),
            "work_dir": "",
        }
        try:
            with contextlib.redirect_stdout(_NullWriter()), contextlib.redirect_stderr(_NullWriter()):
                out["cfg"] = core.apply_config(updates, make_repo=True)
        except BaseException as e:
            messagebox.showerror("配置失败", f"{e}\n\n可以再检查一下路径和仓库地址。", parent=win)
            return
        win.destroy()

    def cancel() -> None:
        win.destroy()

    bar = tk.Frame(win, bg=C["bg"])
    bar.pack(fill="x", padx=18, pady=12)
    _make_button(bar, "保存并继续", save, "primary").pack(side="left")
    _make_button(bar, "取消", cancel, "ghost").pack(side="left", padx=8)
    e_name.focus_set()
    win.grab_set()
    parent.wait_window(win)
    return out["cfg"]


class _NullWriter:
    def write(self, s):
        pass

    def flush(self):
        pass


def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    cfg = core.load_cfg(required=False)

    root = tk.Tk()
    root.withdraw()
    root.title("MC 联机助手 · 1022-1806")
    root.configure(bg=C["bg"])
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    W = min(px(980), int(sw * 0.86))
    H = min(px(690), int(sh * 0.86))
    # 用固定的左上边距而不是"居中"：高 DPI 下 Screen/Tk 的坐标系可能不一致，
    # 居中很容易把窗口推到屏幕外，左上留白永远安全（窗口本身可以拖动）。
    root.geometry(f"{W}x{H}+{px(48)}+{px(36)}")
    root.minsize(min(px(820), W), min(px(560), H))

    # 主题：clam 更好改色
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("MC.TCombobox", fieldbackground=C["card"], background=C["card"],
                    foreground=C["text"], arrowcolor=C["muted"], bordercolor=C["border"],
                    lightcolor=C["border"], darkcolor=C["border"], padding=6)
    style.map("MC.TCombobox",
              fieldbackground=[("readonly", C["card"])],
              foreground=[("readonly", C["text"])],
              bordercolor=[("focus", C["accent"])])
    style.configure("MC.Vertical.TScrollbar", background=C["panel"], troughcolor=C["bg"],
                    bordercolor=C["bg"], arrowcolor=C["muted"])
    root.option_add("*TCombobox*Listbox.background", C["card"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["accent_text"])

    if not cfg:
        # 第一次用（或 exe 版没有黑框）：直接在窗口里填配置表
        cfg = setup_wizard(root)
        if not cfg:
            root.destroy()
            return

    # exe 里 __file__ 指向临时解包目录，读 mtime 没意义（也不一定存在）
    if getattr(sys, "frozen", False):
        CODE_MTIME = 0.0
        try:
            CODE_MTIME = Path(sys.executable).resolve().stat().st_mtime
        except Exception:
            pass
    else:
        try:
            CODE_MTIME = Path(__file__).resolve().stat().st_mtime
        except OSError:
            CODE_MTIME = 0.0
    try:                       # 标题上的编号 = 本程序版本，用来识别"旧窗口"
        ver = (time.strftime("%m%d-%H%M", time.localtime(CODE_MTIME))
               if CODE_MTIME else "exe")
        root.title("MC 联机助手 · " + ver)
    except Exception:
        pass
    # 开窗时顺手扫一遍游戏日志，把"名字 ↔ UUID"自动对上（把从没进过游戏的名字排除在外）
    try:
        core.auto_map_from_logs(cfg)
    except Exception:
        pass
    try:
        with open(APP_DIR / "gui-startup.txt", "a",
                  encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 build="
                    f"{time.strftime('%m%d-%H%M', time.localtime(CODE_MTIME))}\n")
    except Exception:
        pass
    state = {"world": None, "chars": [], "sel": -1, "log_open": True}
    logq: queue.Queue[str] = queue.Queue()

    class Writer:
        def write(self, s):
            if s:
                logq.put(s)

        def flush(self):
            pass

    # ══════════════ 顶部标题栏
    head = tk.Frame(root, bg=C["bg"])
    head.pack(fill="x", padx=16, pady=(12, 4))

    badge = tk.Canvas(head, width=34, height=34, bg=C["bg"], highlightthickness=0)
    badge.pack(side="left")
    badge.create_oval(1, 1, 33, 33, fill=C["accent"], outline="")
    badge.create_text(17, 17, text="MC", fill=C["accent_text"],
                      font=("Microsoft YaHei UI", 11, "bold"))

    tbox = tk.Frame(head, bg=C["bg"])
    tbox.pack(side="left", padx=10)
    tk.Label(tbox, text="联机存档助手", bg=C["bg"], fg=C["text"],
             font=F["title"]).pack(anchor="w")
    tk.Label(tbox, text="谁玩完谁上传 · 下一个开局自动拉最新 · 按名字认人",
             bg=C["bg"], fg=C["muted"], font=F["small"]).pack(anchor="w")

    who = tk.Label(head, text="", bg=C["panel"], fg=C["text"], font=F["small"],
                   padx=12, pady=6, cursor="hand2")
    who.pack(side="right")

    # ══════════════ 游戏进程状态横幅（换角色必须游戏完全退出）
    banner = tk.Label(root, text="检测中…", bg=C["bg"], fg=C["muted"], font=F["small"],
                      anchor="w", padx=16)
    banner.pack(fill="x")

    # ══════════════ 存档选择条
    bar = tk.Frame(root, bg=C["bg"])
    bar.pack(fill="x", padx=16, pady=(2, 8))
    tk.Label(bar, text="存档", bg=C["bg"], fg=C["muted"], font=F["small"]).pack(side="left")
    world_var = tk.StringVar()
    combo = ttk.Combobox(bar, textvariable=world_var, state="readonly", width=34,
                         style="MC.TCombobox", font=F["body"])
    combo.pack(side="left", padx=(8, 10))
    btn_refresh = _make_button(bar, "刷新", lambda: reload_worlds(), "ghost")
    btn_refresh.pack(side="left", padx=(0, 6))
    btn_folder = _make_button(bar, "打开文件夹", lambda: open_folder(), "ghost")
    btn_folder.pack(side="left")

    # ══════════════ 世界信息（三个小方块）
    stats_row = tk.Frame(root, bg=C["bg"])
    stats_row.pack(fill="x", padx=16)
    stat_labels = {}
    for i, (key, cap) in enumerate((("day", "天数"), ("size", "体积"), ("chars", "可选身份"))):
        tile = _card(stats_row, pad=8)
        tile.pack(side="left", padx=(0 if i == 0 else 10, 0))
        num = tk.Label(tile, text="—", bg=C["card"], fg=C["accent"], font=F["num"])
        num.pack(anchor="w")
        tk.Label(tile, text=cap, bg=C["card"], fg=C["muted"], font=F["tiny"]).pack(anchor="w")
        stat_labels[key] = num

    # ══════════════ 主体：角色卡片列表 + 日志
    body = tk.Frame(root, bg=C["bg"])
    body.pack(fill="both", expand=True, padx=16, pady=(10, 0))

    list_wrap = tk.Frame(body, bg=C["bg"])
    list_wrap.pack(side="left", fill="both", expand=True)

    list_head = tk.Frame(list_wrap, bg=C["bg"])
    list_head.pack(fill="x", pady=(0, 6))
    tk.Label(list_head, text="这个存档里的身份", bg=C["bg"], fg=C["text"],
             font=F["h2"]).pack(side="left")
    hint = tk.Label(list_head, text="点一张卡片选中，双击直接进入", bg=C["bg"],
                    fg=C["dim"], font=F["small"])
    hint.pack(side="left", padx=10)

    canvas = tk.Canvas(list_wrap, bg=C["bg"], highlightthickness=0, bd=0)
    vbar = ttk.Scrollbar(list_wrap, orient="vertical", command=canvas.yview,
                         style="MC.Vertical.TScrollbar")
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    vbar.pack(side="right", fill="y")
    inner = tk.Frame(canvas, bg=C["bg"])
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
    canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    # 右侧：角色详情（装备 + 物品栏）
    detail_wrap = tk.Frame(body, bg=C["bg"], width=330)
    detail_wrap.pack(side="right", fill="y", padx=(14, 0))
    detail_wrap.pack_propagate(False)
    dh = tk.Frame(detail_wrap, bg=C["bg"])
    dh.pack(fill="x", pady=(0, 6))
    tk.Label(dh, text="角色详情", bg=C["bg"], fg=C["text"], font=F["h2"]).pack(side="left")
    detail = tk.Text(detail_wrap, bg=C["panel"], fg=C["text"], font=F["small"],
                     wrap="word", bd=0, padx=12, pady=10, insertbackground=C["text"],
                     highlightthickness=1, highlightbackground=C["border"], spacing1=1,
                     spacing3=2)
    ds = ttk.Scrollbar(detail_wrap, orient="vertical", command=detail.yview,
                       style="MC.Vertical.TScrollbar")
    detail.configure(yscrollcommand=ds.set)
    detail.pack(side="left", fill="both", expand=True)
    ds.pack(side="right", fill="y")
    detail.tag_configure("h", foreground=C["accent"], font=("Microsoft YaHei UI", 10, "bold"))
    detail.tag_configure("dim", foreground=C["dim"], font=F["tiny"])
    detail.tag_configure("item", foreground=C["text"])
    detail.tag_configure("name", foreground=C["text"], font=("Microsoft YaHei UI", 11, "bold"))
    detail.configure(state="disabled")

    # 底部抽屉：日志（默认收起）
    log_drawer = tk.Frame(root, bg=C["bg"])
    logbox = tk.Text(log_drawer, bg=C["log_bg"], fg=C["log_text"], font=F["mono"],
                     wrap="none", height=8, bd=0, padx=10, pady=8,
                     insertbackground=C["text"], highlightthickness=1,
                     highlightbackground=C["border"])
    ls = ttk.Scrollbar(log_drawer, orient="vertical", command=logbox.yview,
                       style="MC.Vertical.TScrollbar")
    logbox.configure(yscrollcommand=ls.set)
    logbox.pack(side="left", fill="both", expand=True)
    ls.pack(side="right", fill="y")
    logbox.configure(state="disabled")

    # ══════════════ 底部按钮栏
    foot = tk.Frame(root, bg=C["bg"])
    foot.pack(fill="x", padx=16, pady=10)
    btn_enter = _make_button(foot, "▶  以选中身份进入游戏",
                             lambda: on_enter(), "primary", font=F["body"])
    btn_enter.pack(side="left")
    btn_up = _make_button(foot, "上传存档", lambda: on_upload(), "ghost")
    btn_up.pack(side="left", padx=8)
    btn_pull = _make_button(foot, "拉取存档", lambda: on_pull(), "ghost")
    btn_pull.pack(side="left")
    btn_log = _make_button(foot, "显示日志", lambda: toggle_log(), "ghost")
    btn_log.pack(side="left", padx=8)
    btn_all = _make_button(foot, "显示全部角色", lambda: toggle_all(), "ghost")
    btn_all.pack(side="left")
    btn_name = _make_button(foot, "给选中角色命名", lambda: on_rename(), "ghost")
    btn_name.pack(side="left", padx=8)
    btn_doc = _make_button(foot, "体检", lambda: on_doctor(), "ghost")
    btn_doc.pack(side="left", padx=8)
    btn_op = _make_button(foot, "作弊开关", lambda: on_op(), "ghost")
    btn_op.pack(side="left", padx=8)
    btn_more = _make_button(foot, "更多 ▾", lambda: show_more(), "ghost")
    btn_more.pack(side="left")
    _make_button(foot, "退出", lambda: root.destroy(), "ghost").pack(side="right")

    status = tk.Label(root, text="", bg=C["panel"], fg=C["muted"], font=F["small"],
                      anchor="w", padx=14, pady=6)
    status.pack(fill="x", side="bottom")
    btn_enter.set_enabled(False)

    # ══════════════ 「更多」菜单：以前只能靠 bat/命令行做的事，全塞进窗口
    def show_more() -> None:
        from tkinter import filedialog
        m = tk.Menu(root, tearoff=0, bg=C["card"], fg=C["text"], font=F["body"],
                    activebackground=C["accent"], activeforeground=C["accent_text"],
                    bd=0, relief="flat")

        def do_export() -> None:
            dest = filedialog.askdirectory(title="导出到哪个文件夹（U 盘/桌面都行）")
            if dest:
                do_task("导出存档", lambda: core.export_zip(cfg, Path(dest)))

        def do_import() -> None:
            src = filedialog.askdirectory(title="从哪个文件夹导入（选含 mc-*.zip 的目录）")
            if not src:
                return
            if messagebox.askyesno("确认导入", "会用那个文件夹里最新的存档包覆盖本地存档。\n"
                                             "（覆盖前会自动备份到 backups\\）继续吗？"):
                do_task("导入存档", lambda: core.import_zip(cfg, Path(src), force=True))

        def do_import_old() -> None:
            src = filedialog.askdirectory(title="选旧存档文件夹（里面有 level.dat 那一层）")
            if src:
                do_task("从旧存档导入玩家数据",
                        lambda: core.cmd_import_players(
                            core.argparse.Namespace(from_world=src, force=True)))

        def do_reconfig() -> None:
            messagebox.showinfo(
                "重新配置",
                f"配置文件在：\n{core.CONFIG_PATH}\n\n"
                "改完保存，重开窗口生效。\n"
                "只想改名字的话，点右上角「我的 ID」就行。")

        def do_open(path) -> None:
            try:
                os.startfile(str(path))
            except Exception as e:
                messagebox.showwarning("打不开", str(e))

        m.add_command(label="上传存档（推到 GitHub）", command=lambda: on_upload())
        m.add_command(label="拉取存档（用远端的）", command=lambda: on_pull())
        m.add_separator()
        m.add_command(label="导出存档到文件夹（拷给朋友 / U 盘）", command=do_export)
        m.add_command(label="从文件夹导入存档", command=do_import)
        m.add_command(label="从旧存档导入玩家数据", command=do_import_old)
        m.add_separator()
        m.add_command(label="改我的 ID", command=on_change_me)
        m.add_command(label="体检", command=on_doctor)
        m.add_command(label="打开备份文件夹", command=lambda: do_open(core.BACKUP_DIR))
        m.add_command(label="打开 MCMesh 文件夹", command=lambda: do_open(APP_DIR))
        m.add_command(label="配置文件在哪（重新配置）", command=do_reconfig)
        m.add_separator()
        m.add_command(label="关于", command=lambda: messagebox.showinfo(
            "关于",
            "MCMesh 联机存档助手\n"
            f"工作目录：{APP_DIR}\n"
            f"配置文件：{core.CONFIG_PATH}\n"
            f"存档：{core.world_dir(cfg)}\n\n"
            "谁玩完谁上传；下一个人开局自动拉最新；按名字认人。"))
        try:
            m.tk_popup(btn_more.winfo_rootx(), btn_more.winfo_rooty() - 8)
        finally:
            m.grab_release()


    # ══════════════ 行为
    def log_text(s: str) -> None:
        logbox.configure(state="normal")
        logbox.insert("end", s)
        logbox.see("end")
        logbox.configure(state="disabled")

    def pump() -> None:
        while True:
            try:
                log_text(logq.get_nowait())
            except queue.Empty:
                break
        root.after(120, pump)

    def set_status(s: str) -> None:
        status.configure(text=s)

    busy = {"v": False}

    def do_task(title: str, fn, then=None) -> None:
        if busy["v"]:
            messagebox.showinfo("稍等", "上一个操作还没结束")
            return
        busy["v"] = True
        root.after(120000, lambda: busy.update(v=False))
        set_status(f"{title}…")

        def worker():
            try:
                with contextlib.redirect_stdout(Writer()), contextlib.redirect_stderr(Writer()):
                    print(f"\n──── {title} ────")
                    fn()
            except SystemExit:
                pass
            except Exception:
                print("\n" + traceback.format_exc())
            finally:
                busy["v"] = False
                root.after(0, lambda: (set_status(f"完成：{title}"), refresh_chars(),
                                       then and then()))

        threading.Thread(target=worker, daemon=True).start()

    def current_world() -> Path | None:
        name = world_var.get()
        return core.instance_dir(cfg) / "saves" / name if name else None

    def open_folder() -> None:
        w = current_world()
        target = w if (w and w.exists()) else (core.instance_dir(cfg) / "saves")
        try:
            os.startfile(str(target))
        except Exception as e:
            set_status(f"打不开文件夹：{e}")

    # ---- 卡片
    def build_cards() -> None:
        for ch in inner.winfo_children():
            ch.destroy()
        state["sel"] = -1
        allrows = state["chars"]
        chars = [r for r in allrows if state.get("show_all") or r["in_world"]]
        hidden = len(allrows) - len(chars)
        if not chars:
            empty = tk.Frame(inner, bg=C["bg"], pady=26)
            empty.pack(fill="x")
            tk.Label(empty, text="这个存档里还没有记录过任何人", bg=C["bg"],
                     fg=C["muted"], font=F["body"]).pack()
            tip = ("点下面的「显示全部角色」可以看到其它存档里的角色"
                   if hidden else "先去游戏里玩一局，玩完点「上传存档」，角色就会出现在这里")
            tk.Label(empty, text=tip, bg=C["bg"], fg=C["dim"], font=F["small"]).pack(pady=(4, 0))
            btn_enter.set_enabled(False)
            clear_detail()
            return

        if hidden:
            note = tk.Label(inner, text=f"已隐藏 {hidden} 个不属于这个存档的角色"
                                        f"（点「显示全部角色」可以看）",
                            bg=C["bg"], fg=C["dim"], font=F["tiny"], anchor="w")
            note.pack(fill="x", pady=(0, 6))

        card_widgets: list = []
        card_selectors: list = []
        for i, r in enumerate(chars):
            card = _card(inner, pad=0)
            card.pack(fill="x", pady=(0, 8))
            card_widgets.append(card)

            row = tk.Frame(card, bg=C["card"])
            row.pack(fill="x", padx=14, pady=11)

            left = tk.Frame(row, bg=C["card"])
            left.pack(side="left", fill="x", expand=True)

            name_row = tk.Frame(left, bg=C["card"])
            name_row.pack(anchor="w")

            # ① 身份标签（房主 / 访客 / 记录）
            role = r.get("role", "记录")
            role_color = {"房主": (C["accent"], C["accent_text"]),
                          "访客": (C["blue"], "#08131f")}.get(role, (C["panel"], C["muted"]))
            tk.Label(name_row, text=role, bg=role_color[0], fg=role_color[1],
                     font=F["tiny"], padx=7, pady=1).pack(side="left", padx=(0, 8))

            # ② 这个人的 ID
            tk.Label(name_row, text=r.get("ident", r["label"]), bg=C["card"], fg=C["text"],
                     font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")

            # ③ 来源 / 成就 / 其它存档
            for src in r["sources"]:
                tag = tk.Label(name_row, text=src, bg=C["panel"], fg=C["blue"],
                               font=F["tiny"], padx=6, pady=1)
                tag.pack(side="left", padx=(6, 0))
            if r.get("uuid"):
                tk.Label(name_row, text=r["uuid"][:8] + "…", bg=C["card"], fg=C["dim"],
                         font=F["tiny"]).pack(side="left", padx=(6, 0))
            if r["ach"] == "已存":
                tk.Label(name_row, text="含成就", bg=C["panel"], fg=C["accent"],
                         font=F["tiny"], padx=6, pady=1).pack(side="left", padx=(6, 0))
            if not r["usable"]:
                tk.Label(name_row, text="只读", bg=C["card"], fg=C["dim"],
                         font=F["tiny"]).pack(side="left", padx=6)
            if r.get("current"):
                tk.Label(name_row, text="当前角色", bg=C["accent"], fg=C["accent_text"],
                         font=F["tiny"], padx=6, pady=1).pack(side="left", padx=(6, 0))
            if not r["in_world"]:
                tk.Label(name_row, text="其它存档", bg=C["card"], fg=C["danger"],
                         font=F["tiny"], padx=4, pady=1).pack(side="left", padx=(6, 0))

            p = r["parts"]
            detail_line = f"坐标 {p.get('坐标','?')}    等级 {p.get('等级','?')}    背包 {p.get('背包','?')}"
            tk.Label(left, text=detail_line, bg=C["card"], fg=C["muted"],
                     font=F["small"]).pack(anchor="w", pady=(5, 0))

            right = tk.Frame(row, bg=C["card"])
            right.pack(side="right")
            tk.Label(right, text="Lv", bg=C["card"], fg=C["dim"], font=F["tiny"]).pack()
            tk.Label(right, text=str(p.get("等级", "?")), bg=C["card"], fg=C["accent"],
                     font=("Microsoft YaHei UI", 15, "bold")).pack()

            def select(idx=i, row=r):
                state["sel"] = idx
                # 只按"卡片控件列表"高亮，绝不按容器子控件的位置算（加了提示行就会错位一格）
                for j, c in enumerate(card_widgets):
                    on = (j == idx)
                    c.configure(highlightbackground=C["border_sel"] if on else C["border"],
                                highlightthickness=2 if on else 1,
                                bg=C["card_sel"] if on else C["card"])
                    paint_children(c, C["card_sel"] if on else C["card"])
                if row.get("current"):
                    btn_enter.set_enabled(False)
                    set_status(f"「{row['ident']}」就是当前存档里的角色 —— 想换身体请选另一张卡片")
                else:
                    btn_enter.set_enabled(bool(row.get("payload")))
                    set_status(f"已选中「{row['ident']}」（{row['role']}）—— 右侧是它的装备和物品栏")
                fill_detail(row)

            def bind_all(widget, fn, dbl=True):
                widget.bind("<Button-1>", lambda e: fn())
                if dbl:
                    widget.bind("<Double-1>", lambda e: on_enter())
                for ch2 in widget.winfo_children():
                    bind_all(ch2, fn, dbl)

            bind_all(card, select)
            card_selectors.append(select)

        # 无头自检：MCMESH_GUI_SELFTEST=1 时，自动点一张"有装备"的卡片并把结果写文件
        if os.environ.get("MCMESH_GUI_SELFTEST") and len(card_selectors) >= 1:
            def _probe():
                idx = 0
                for j, r in enumerate(chars):
                    if "背包 0 件" not in (r.get("summary") or ""):
                        idx = j
                        break
                card_selectors[idx]()
                root.update_idletasks()
                hl = [j for j, c in enumerate(card_widgets)
                      if str(c.cget("highlightbackground")) == C["border_sel"]]
                txt = detail.get("1.0", "end").strip()
                try:
                    (APP_DIR / "gui-probe.txt").write_text(
                        f"clicked_index={idx}\nhighlighted={hl}\n"
                        f"expected={chars[idx]['ident']}\n"
                        f"detail_has_items={'【' in txt}\n--- detail ---\n{txt}\n",
                        encoding="utf-8")
                except Exception:
                    pass
                root.destroy()
            root.after(400, _probe)

    # ---- 右侧详情
    def clear_detail() -> None:
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("end", "点一张卡片，这里会显示它的装备和物品栏\n", "dim")
        detail.configure(state="disabled")

    def fill_detail(row) -> None:
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("end", f"{row['ident']}\n", "name")
        detail.insert("end", f"{row['role']} · {'/'.join(row['sources'])}\n", "dim")
        detail.insert("end", f"{row['summary']}\n", "dim")
        if row.get("uuid"):
            detail.insert("end", f"UUID {row['uuid'][:8]}…\n", "dim")
        if row.get("ach") == "已存":
            detail.insert("end", "成就/统计：随角色保存\n", "dim")
        detail.insert("end", "\n")
        try:
            lines = core.inventory_lines(row["payload"])
        except Exception as e:
            lines = [f"（物品栏读不出来：{e}）"]
        for line in lines:
            if line.startswith("【"):
                detail.insert("end", line + "\n", "h")
            elif line.startswith("  "):
                detail.insert("end", line + "\n", "item")
            else:
                detail.insert("end", line + "\n", "item")
        detail.configure(state="disabled")

    # ---- 世界
    def bind_world() -> None:
        """把下拉框里选中的存档同步到配置 —— 否则换角色/上传都作用在"配置里那个世界"。"""
        name = world_var.get()
        if name and name != cfg.get("world"):
            cfg["world"] = name
            try:
                core.save_cfg(cfg)
            except Exception:
                pass
            set_status(f"当前存档已切到：{name}")

    def refresh_chars(*_):
        bind_world()
        w = current_world()
        if not w:
            state["chars"] = []
            build_cards()
            clear_detail()
            return
        state["chars"] = collect_characters(cfg, w)
        meta = next((x for x in collect_worlds(cfg) if x["name"] == w.name), None)
        stat_labels["day"].configure(text=str(meta["day"]) if meta else "—")
        stat_labels["size"].configure(text=meta["size"] if meta else "—")
        stat_labels["chars"].configure(text=str(len(state["chars"])))
        build_cards()
        clear_detail()
        set_status(f"{w.name}：{len(state['chars'])} 个身份可选 —— 选一个，然后点左下角绿色按钮")

    def reload_worlds(select: str = "") -> None:
        worlds = collect_worlds(cfg)
        names = [x["name"] for x in worlds]
        combo["values"] = names
        if select and select in names:
            world_var.set(select)
        elif names:
            cur = cfg.get("world")
            world_var.set(cur if cur in names else names[0])
        who.configure(text=f"  {cfg['player']}  ✎  ·  "
                           f"{'局域网' if cfg['mode'] == 'lan' else '专用服'}  ")
        refresh_chars()

    # ---- 动作
    # ---- 游戏是否在运行（换角色必须游戏完全退出）
    def game_check(notify: bool = False) -> bool:
        procs = core.java_processes()
        if procs:
            banner.configure(
                text=f"● 检测到 Minecraft 正在运行（{'、'.join(procs)}）"
                     f"　→ 换角色前请先在 PCL 里完全关闭游戏",
                fg=C["danger"])
        else:
            banner.configure(text="● 游戏未运行，可以换角色", fg=C["accent"])
        if notify and procs:
            try:
                import datetime
                with open(APP_DIR / "switch-error.txt", "a",
                          encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] "
                            f"游戏仍在运行：{procs}\n")
            except Exception:
                pass
        return bool(procs)

    PENDING_PATH = APP_DIR / "pending-switch.json"

    def load_pending():
        try:
            if PENDING_PATH.is_file():
                return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def save_pending(data) -> None:
        try:
            if data is None:
                if PENDING_PATH.is_file():
                    PENDING_PATH.unlink()
            else:
                PENDING_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        except Exception:
            pass

    def tick() -> None:
        try:
            if (not getattr(sys, "frozen", False)
                    and Path(__file__).resolve().stat().st_mtime != CODE_MTIME):
                banner.configure(text="⚠ 工具代码已更新 —— 你现在开的是旧窗口，请关掉重新双击",
                                 fg=C["danger"])
                root.after(8000, tick)
                return
        except Exception:
            pass
        running = game_check()
        pend = load_pending()
        if pend:
            label = pend.get("name") or pend.get("ident") or ("修成就/作弊" if pend.get("fix") else "")
            if running:
                banner.configure(text=f"● 已排队：关掉游戏后自动执行「{label}」（游戏还在运行）",
                                 fg=C["blue"])
                btn_enter.set_enabled(False)
            elif pend.get("fix"):
                # 游戏已退出 → 执行排队的"修成就 + 开作弊"（等它把最后的存档写完再动）
                wd0 = core.world_dir(cfg)
                lvl = wd0 / "level.dat"
                try:
                    settle = (lvl.stat().st_mtime if lvl.exists() else 0)
                except OSError:
                    settle = 0
                if settle and time.time() - settle < 3:
                    banner.configure(text="● 游戏刚退出，等它存完盘再自动修（几秒）", fg=C["blue"])
                    root.after(2000, tick)
                    return
                save_pending(None)
                log_text("\n[排队] 游戏已退出，开始执行 fix（成就绑定 + 允许作弊）\n")
                try:
                    x = core.run_fix(cfg)
                    ok_txt = (f"成就已绑到「{x['owner']}」：{x['adv']} 条；"
                              f"「允许作弊」{x['op']}")
                    log_text(f"[OK] {ok_txt}\n")
                    set_status(ok_txt)
                    messagebox.showinfo("修好了", ok_txt + "\n\n现在可以重进游戏了。")
                except BaseException as e:
                    log_text(f"[X] fix 失败：{e}\n")
                    set_status(f"fix 失败：{e}")
                refresh_chars()
            else:
                # 游戏已退出 → 自动执行排队的换角色
                save_pending(None)
                row = next((r for r in state["chars"]
                            if (r.get("usable") or r.get("ident")) == (pend.get("name") or pend.get("ident"))), None)
                if row:
                    log_text(f"[排队] 游戏已退出，开始换成「{row['ident']}」\n")
                    do_switch(row, allow_running=True)
                else:
                    set_status("排队换角色失败：这个角色已经不在列表里了")
        elif running:
            btn_enter.set_enabled(False)
        elif state["sel"] >= 0 and state["chars"]:
            btn_enter.set_enabled(bool(state["chars"][state["sel"]].get("payload")))

        # 游戏没在跑时，自动补一次"成就绑定 + 允许作弊"（= 以前那个 自动修复.bat）
        if not pend and not running and not busy["v"]:
            try_auto_fix()
        root.after(8000, tick)

    # ---- 自动修复（不需要额外开窗口了）
    AUTO = {"last": None, "busy": False, "ticks": 0}

    def try_auto_fix() -> None:
        AUTO["ticks"] += 1
        if AUTO["busy"] or AUTO["ticks"] % 3:      # 每 3 个 tick（约 24 秒）看一次，别太频繁
            return
        try:
            st = core.aux_status(cfg, core.world_dir(cfg))
        except BaseException:
            return
        need = (st.get("op") != 1) or (st.get("merged_adv", -1) > st.get("live_adv", -1) >= 0)
        stamp = (st.get("owner"), st.get("merged_adv"), st.get("live_adv"), st.get("op"))
        if not need or stamp == AUTO["last"]:
            return
        AUTO["busy"] = True

        def work():
            try:
                r = core.run_fix(cfg)
                msg = f"自动修好了：成就 {r['adv']} 条（{r['owner']}） / 允许作弊 {r['op']}"
            except BaseException as e:
                msg = f"自动修复没成功：{e}"

            def done():
                AUTO["busy"] = False
                AUTO["last"] = stamp
                log_text("\n[自动] " + msg + "\n")
                set_status(msg)
                refresh_chars()
            root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    # ---- 换角色：成功/失败都要看得见
    def on_switch_ok(row, res) -> None:
        busy["v"] = False
        if res.get("unchanged"):
            log_text(f"[=] 「{row['ident']}」就是当前存档里的角色，没有变化\n")
            set_status(f"没有变化：「{row['ident']}」本来就是当前角色")
            messagebox.showinfo(
                "没有变化",
                f"「{row['ident']}」就是当前存档里装着的那个角色，所以不用切换。\n\n"
                f"想换另一个身体，请选列表里**另一张**卡片。")
            refresh_chars()
            return
        log_text(f"[OK] 已换成「{row['ident']}」（{res['how']}）：{res['summary']}\n")
        for n in res["notes"]:
            log_text("     " + n + "\n")
        set_status(f"已换成「{row['ident']}」—— 游戏应该正在启动")
        refresh_chars()
        messagebox.showinfo(
            "换好了",
            f"已换成「{row['ident']}」\n{res['summary']}\n\n游戏应该正在启动，没反应就手动开 PCL。")

    def on_switch_fail(msg: str) -> None:
        busy["v"] = False
        log_text(f"[X] {msg}\n")
        set_status(msg)
        try:                       # 失败原因落盘，方便事后排查
            import datetime
            with open(APP_DIR / "switch-error.txt", "a",
                      encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        except Exception:
            pass
        if not log_drawer.winfo_ismapped():
            toggle_log()
        messagebox.showerror("换角色失败", msg + "\n\n详细日志已经展开在窗口底部。")

    def on_switch_running(row, why: str) -> None:
        busy["v"] = False
        log_text(f"[!] {why}\n")
        if messagebox.askyesno(
                "游戏可能还开着",
                why + "\n\n如果你确认游戏已经完全退出，点「是」强制继续；否则点「否」先去退游戏。"):
            do_switch(row, allow_running=True)
        else:
            set_status("已取消 —— 请先完全退出游戏再试")

    def do_switch(row, allow_running: bool = False) -> None:
        bind_world()
        busy["v"] = True
        root.after(25000, lambda: busy.update(v=False))   # 兜底：不让忙锁卡死界面
        set_status("换身份中…")
        log_text(f"\n──── 换角色：{row['ident']} ────\n")

        def work():
            try:
                core.auto_map_from_logs(cfg, force=True)   # 每次都重新扫日志，名字尽量对全
            except Exception:
                pass
            try:
                res = core.switch_character(cfg, row["usable"] or "", payload=row["payload"],
                                            allow_running=allow_running)
            except RuntimeError as e:                     # 游戏可能还开着
                root.after(0, lambda: on_switch_running(row, str(e)))
                return
            except BaseException as e:                    # 包括 die() 抛的 SystemExit
                msg = str(e) or e.__class__.__name__
                root.after(0, lambda: on_switch_fail(f"换角色失败：{msg}"))
                return
            root.after(0, lambda: on_switch_ok(row, res))
            try:
                core.launch_client(cfg)
            except BaseException:
                pass

        threading.Thread(target=work, daemon=True).start()

    def on_enter() -> None:
        if state["sel"] < 0 or not state["chars"]:
            messagebox.showinfo("提示", "先点一张卡片选中身份")
            return
        if busy["v"]:
            messagebox.showinfo("稍等", "上一个操作还没结束")
            return
        r = state["chars"][state["sel"]]
        if not r.get("payload"):
            messagebox.showinfo("提示", "这份角色读不出来，换一个")
            return
        if game_check(notify=True):
            if messagebox.askyesno(
                    "游戏还开着",
                    "检测到 Minecraft 进程还在（窗口标题就是 Better MC）。\n"
                    "换角色要改写 level.dat，游戏开着一定写不进去。\n\n"
                    "要不要【排队】？等你关掉游戏后，它会自动换成这个身份。"):
                save_pending({"name": r.get("usable") or "", "ident": r["ident"],
                              "world": world_var.get()})
                log_text(f"[排队] 已记录：游戏退出后自动换成「{r['ident']}」\n")
                messagebox.showinfo(
                    "已排队",
                    f"好的。你把游戏关掉后（进程消失），这个窗口会在 4 秒内自动完成切换。\n\n"
                    f"要换成的身份：{r['ident']}")
                set_status(f"已排队：关掉游戏后自动换成「{r['ident']}」")
            else:
                set_status("已取消 —— 请先在 PCL 里完全关闭游戏")
            return
        if not messagebox.askyesno(
            "确认换身份",
            f"把存档里的角色换成：\n\n{r['ident']}（{r['role']}）\n{r['summary']}\n\n"
            "前提：游戏已经完全退出（否则保存时会被盖回去）。\n继续吗？"):
            return
        do_switch(r)

    def on_upload() -> None:
        do_task("上传存档", lambda: core.cmd_push(
            core.argparse.Namespace(message=None, force=False, to_dir=None)))

    def on_pull() -> None:
        try:
            dirty = core.local_dirty_vs_sync(cfg)
        except Exception:
            dirty = False
        if dirty and not messagebox.askyesno(
            "本地有未上传的进度",
            "本地存档和上次同步时不一样。\n拉取会覆盖它（会先自动备份）。继续吗？"):
            return
        do_task("拉取存档", lambda: core.cmd_pull(
            core.argparse.Namespace(force=True, from_dir=None)))

    def on_rename() -> None:
        if state["sel"] < 0 or not state["chars"]:
            messagebox.showinfo("提示", "先点一张卡片选中")
            return
        r = state["chars"][state["sel"]]
        uid = (r.get("uuid") or "").lower()
        if not uid:
            messagebox.showinfo("提示", "这份角色没有 UUID（来自 chars 记录），不用命名")
            return
        from tkinter import simpledialog
        name = simpledialog.askstring(
            "给这个角色命名",
            f"这个角色的 UUID：\n{uid}\n\n他是谁？（填游戏里的 ID）",
            parent=root)
        if not name or not name.strip():
            return
        name = name.strip()
        core.register_player(cfg, name, "member", "手动命名", uid=uid)
        core.add_alias(cfg, name, uid, "手动命名")
        refresh_chars()
        set_status(f"已把 {uid[:8]}… 记成「{name}」——以后显示名字")

    def toggle_all() -> None:
        state["show_all"] = not state.get("show_all", False)
        btn_all.configure(text="只看本存档" if state["show_all"] else "显示全部角色")
        build_cards()

    def toggle_log() -> None:
        if log_drawer.winfo_ismapped():
            log_drawer.pack_forget()
            btn_log.configure(text="显示日志")
        else:
            log_drawer.pack(fill="x", padx=16, pady=(6, 0), before=foot)
            btn_log.configure(text="隐藏日志")

    def on_doctor() -> None:
        do_task("体检", lambda: core.cmd_doctor(core.argparse.Namespace()))

    # ══════════════ 改我的默认 ID（换账号 / 给朋友用之前先改这个）
    def on_change_me() -> None:
        from tkinter import simpledialog
        cur = cfg.get("player", "")
        new = simpledialog.askstring(
            "我的 ID",
            f"现在的默认 ID：{cur}\n\n"
            "这是「你在这个世界里的名字」，必须和你在游戏里的 ID 完全一致。\n"
            "换账号、或者把这个工具给朋友用时，先改成对应的名字。\n\n"
            "注意：改名 = 换离线 UUID = 游戏里会变成另一个角色；\n"
            "原来那具身体还在旧名字下，列表里选旧名字那张卡就能接着玩。",
            initialvalue=cur)
        if new is None:
            return
        new = new.strip()
        if not new or new == cur:
            return
        if any(ch.isspace() for ch in new) or len(new) > 16:
            messagebox.showwarning("名字不对", "不能有空格，长度别超过 16 个字符。")
            return
        old_entry = ((core.load_players(cfg).get("players") or {}).get(cur) or {})
        cfg["player"] = new
        core.save_cfg(cfg)
        try:
            core.register_player(cfg, new, old_entry.get("role") or "owner",
                                 (f"本机（原 {cur}）" if cur else "本机"))
        except BaseException:
            pass
        reload_worlds()
        set_status(f"默认 ID 已改成「{new}」—— 之后都用这个身份；旧名字「{cur}」的角色还在列表里")
        log_text(f"\n──── 默认 ID：{cur} → {new} ────\n")
        messagebox.showinfo(
            "改好了",
            f"默认 ID：{new}\n"
            f"离线 UUID：{core.offline_uuid(new)}\n\n"
            f"· 以后「以选中身份进入游戏」、上传都用这个名字\n"
            f"· 旧名字「{cur}」那具身体没动，选那张卡还能接着玩\n"
            f"· 想把它改成新名字：给选中角色命名 / chars --as {new}")

    # ══════════════ 管理员权限（单机存档的「允许作弊」）
    def on_op() -> None:
        bind_world()
        wd = core.world_dir(cfg)
        cur = core.allow_commands(wd)
        now = "已打开（/gamemode、/give、/tp 都能用）" if cur == 1 else "已关闭（命令全用不了）"
        if game_check(notify=False):
            # 游戏开着改一定被覆盖 → 直接排队，等退出后自动做
            if messagebox.askyesno(
                    "游戏还开着",
                    "游戏一存档就会把它内存里的旧状态写回 level.dat，\n"
                    "现在改等于白改（「允许作弊」又会变回关闭）。\n\n"
                    "要不要【排队】？等你退出游戏后，它会自动帮你打开「允许作弊」"
                    "并把成就绑到当前身份。\n\n"
                    "（想在游戏里马上有权限：ESC → 对局域网开放 → 允许作弊：开）"):
                save_pending({"fix": True, "name": "修成就/作弊"})
                set_status("已排队：退出游戏后自动打开「允许作弊」并修成就")
                log_text("\n[排队] 退出游戏后自动执行 fix（成就绑定 + 允许作弊）\n")
                banner.configure(text="● 已排队：关掉游戏后自动修成就 + 开「允许作弊」",
                                 fg=C["blue"])
            return
        want = messagebox.askyesno(
            f"作弊开关 · 现在是：{now}",
            "这是单机存档的「允许作弊」总开关，也就是单机里的管理员权限。\n\n"
            f"要把它{'关掉' if cur == 1 else '打开'}吗？\n\n"
            "· 只改这一个开关，不动难度、模式、角色数据\n"
            "· 打开后进游戏可用 /gamemode creative、/give、/tp 等\n"
            "· 想给自己 op：进游戏按 T 输入 /op 你的名字\n"
            "· 顺便会把成就/统计重新绑到「游戏真正在用的身份」上")
        if not want:
            return
        try:
            how = core.set_allow_commands(wd, cur != 1)
        except SystemExit as e:
            set_status(str(e) or "改不了，先退出游戏")
            return
        except BaseException as e:
            set_status(f"改不了：{e}")
            return
        set_status(f"「允许作弊」{how} —— 下次进游戏生效")
        log_text(f"\n──── 作弊开关：{how} ────\n")
        # 顺手把成就绑到当前身份（很多"成就没绑上"就是这个原因）
        try:
            host = core.get_host_char_payload(wd)
            owner = (core.identify_payload_owner(cfg, host)
                     or core.load_state().get("host_char_owner") or "")
            if owner:
                core.save_char_aux(cfg, owner, wd)
                got = core.apply_char_aux(cfg, owner, wd)
                live = core.live_player_uuid(cfg, wd)
                f = wd / "advancements" / f"{live}.json"
                n = core._aux_done(f) if f.is_file() else -1
                set_status(f"「允许作弊」{how}；成就已绑到「{owner}」：{n} 条"
                           if got else f"「允许作弊」{how}；成就档案里还没有 {owner} 的数据")
        except BaseException as e:
            log_text(f"成就重绑失败：{e}\n")
        reload_worlds()

    # ══════════════ 启动
    who.bind("<Button-1>", lambda e: on_change_me())
    combo.bind("<<ComboboxSelected>>", refresh_chars)
    pump()
    root.deiconify()
    root.update_idletasks()
    tick()
    try:
        (APP_DIR / "gui-diag.txt").write_text(
            f"screen={root.winfo_screenwidth()}x{root.winfo_screenheight()}  "
            f"win={root.winfo_width()}x{root.winfo_height()}  "
            f"at={root.winfo_rootx()},{root.winfo_rooty()}  scale={SC}\n",
            encoding="utf-8")
    except Exception:
        pass
    root.lift()
    root.attributes("-topmost", True)
    root.after(1200, lambda: root.attributes("-topmost", False))
    reload_worlds()
    log_text("就绪。选存档 → 选身份 → 进入游戏。\n")
    root.mainloop()


# ────────────────────────────────────────────────── 自测

def selftest() -> None:
    cfg = core.load_cfg(required=False)
    if not cfg:
        print("还没有配置")
        return
    worlds = collect_worlds(cfg)
    print(f"存档：{len(worlds)} 个")
    for w in worlds:
        print(f"  {w['name']}  第 {w['day']} 天  {w['size']}  {w['players']} 个玩家数据")
    for w in worlds[:1]:
        print(f"\n「{w['name']}」里的身份：")
        for r in collect_characters(cfg, w["path"]):
            print(f"  {r['label']:16} {r['summary']:46} 来源={','.join(r['sources']):26} "
                  f"可切换={'是' if r['usable'] else '否'} 成就={r['ach']}")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    try:
        run_gui()
    except Exception:
        try:
            (APP_DIR / "gui-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
