# -*- coding: utf-8 -*-
"""MCMesh 的唯一入口：双击 = 打开窗口；带参数 = 走命令行（给 bat / 高级用户用）。"""
import sys


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] not in ("--gui",):
        import mcmesh
        sys.argv = ["mcmesh.py"] + args
        try:
            mcmesh.main()
        except SystemExit as e:
            raise SystemExit(e.code if isinstance(e.code, int) else 0)
        return
    import mcmesh_gui
    mcmesh_gui.main()


if __name__ == "__main__":
    main()
