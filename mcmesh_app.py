# -*- coding: utf-8 -*-
"""MCMesh 的唯一入口。

· 双击（没有参数）      → 打开窗口界面
· 带参数（pull/push/fix…）→ 走命令行

打包成 --windowed 的 exe 时没有控制台，`print` 会没有去处。
所以带参数跑的时候，把输出重定向到 ~/.mcmesh/last-run.log，
别的程序（比如游戏里的模组）读这个文件就能显示进度和结果。
"""
import io
import sys


def _log_path():
    try:
        from pathlib import Path
        d = Path.home() / ".mcmesh"
        d.mkdir(parents=True, exist_ok=True)
        return d / "last-run.log"
    except Exception:
        return None


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] not in ("--gui",):
        log_file = None
        if getattr(sys, "frozen", False) and sys.stdout is None:
            path = _log_path()
            if path is not None:
                try:
                    log_file = open(path, "w", encoding="utf-8", buffering=1)
                    sys.stdout = log_file
                    sys.stderr = log_file
                except OSError:
                    sys.stdout = io.StringIO()
                    sys.stderr = sys.stdout
            else:
                sys.stdout = io.StringIO()
                sys.stderr = sys.stdout
        try:
            import mcmesh
            sys.argv = ["mcmesh.py"] + args
            try:
                mcmesh.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 0
                if log_file is not None:
                    log_file.flush()
                    log_file.close()
                raise SystemExit(code)
        finally:
            if log_file is not None and not log_file.closed:
                log_file.flush()
                log_file.close()
        return
    import mcmesh_gui
    mcmesh_gui.main()


if __name__ == "__main__":
    main()
