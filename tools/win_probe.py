# -*- coding: utf-8 -*-
"""列出某个进程（或所有进程）的顶层窗口：标题/可见性/位置。用于排查"双击没反应"。"""
import ctypes
import ctypes.wintypes as w
import sys

user32 = ctypes.windll.user32

EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def dump(pid_filter=None):
    rows = []

    def cb(hwnd, lparam):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if not title:
            return True
        if pid_filter and pid.value != pid_filter:
            return True
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        rows.append((pid.value, title, bool(user32.IsWindowVisible(hwnd)),
                     r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return rows


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = dump(want)
    if not rows:
        print("（没有标题非空的顶层窗口）")
    for pid, title, vis, x, y, wd, ht in rows:
        print(f"PID {pid:<7} 可见={vis!s:<5} 位置=({x},{y}) 大小={wd}x{ht}  标题={title!r}")
