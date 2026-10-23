# -*- coding: utf-8 -*-
"""把 使用说明书.md 转成 使用说明书.txt（去掉 markdown 记号，控制台/记事本友好）。"""
import re
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
src = (BASE / "使用说明书.md").read_text(encoding="utf-8")

out = []
in_code = False
for raw in src.splitlines():
    line = raw.rstrip()
    if line.startswith("```"):
        in_code = not in_code
        out.append("-" * 72)
        if not in_code:
            out.append("")
        continue
    if in_code:
        out.append("  " + line if line else "")
        continue
    if not line:
        out.append("")
        continue
    if re.match(r"^---+$", line.strip()):
        out.append("=" * 72)
        continue
    if line.startswith("#"):
        level = len(line) - len(line.lstrip("#"))
        title = line[level:].strip()
        if level == 1:
            out.append("=" * 72)
            out.append(title.center(60))
            out.append("=" * 72)
        elif level == 2:
            out.append("")
            out.append("=" * 72)
            out.append(title)
            out.append("=" * 72)
        elif level == 3:
            out.append("")
            out.append("■ " + title)
        else:
            out.append("  · " + title)
        continue
    if line.lstrip().startswith("|"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
            continue
        out.append("  " + "  |  ".join(cells))
        continue
    txt = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)      # [文字](链接) → 文字
    txt = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    txt = re.sub(r"`([^`]*)`", r"\1", txt)
    txt = re.sub(r"^\s*>\s?", "", txt)                        # 引用块
    txt = re.sub(r"^\s*[-*]\s+", "  · ", txt)
    txt = re.sub(r"^\s*(\d+)\.\s+", r"  \1. ", txt)
    out.append(txt)

text = "\n".join(out)
text = re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"
(BASE / "使用说明书.txt").write_text(text, encoding="utf-8")
print("写出 使用说明书.txt：", len(text), "字符")
