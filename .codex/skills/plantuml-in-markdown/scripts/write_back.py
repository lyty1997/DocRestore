#!/usr/bin/env python3
"""把修正后的 .puml 文件按顺序写回 markdown 中对应的 ```plantuml 代码块。

用法:
    python3 write_back.py <markdown_file> <puml_dir>

<puml_dir> 下应包含 g01.puml, g02.puml, ... 与 markdown 中出现顺序对应。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    md_path = Path(sys.argv[1])
    puml_dir = Path(sys.argv[2])

    if not md_path.is_file():
        print(f"ERROR: markdown not found: {md_path}", file=sys.stderr)
        return 2
    if not puml_dir.is_dir():
        print(f"ERROR: puml dir not found: {puml_dir}", file=sys.stderr)
        return 2

    content = md_path.read_text(encoding="utf-8")
    pattern = re.compile(r"```plantuml\n@startuml.*?@enduml\n```", re.DOTALL)
    blocks = pattern.findall(content)
    puml_files = sorted(puml_dir.glob("g*.puml"))

    if len(blocks) != len(puml_files):
        print(
            f"ERROR: markdown has {len(blocks)} plantuml blocks "
            f"but {len(puml_files)} .puml files found",
            file=sys.stderr,
        )
        return 1

    for old, pf in zip(blocks, puml_files):
        new_body = pf.read_text(encoding="utf-8").rstrip("\n")
        new = f"```plantuml\n{new_body}\n```"
        content = content.replace(old, new, 1)
        print(f"replaced <- {pf.name}")

    md_path.write_text(content, encoding="utf-8")

    verify = pattern.findall(md_path.read_text(encoding="utf-8"))
    print(f"OK: {len(verify)} plantuml blocks now in {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
