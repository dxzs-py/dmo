"""临时脚本：检测 Python 文件中未使用的顶层导入。

仅做粗略检测：只检查导入的名称在文件其他位置（非 import 行）是否出现。
对 __all__、字符串引用、re-export 等情况可能误报，需人工确认。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def collect_imports(tree):
    """返回 (import_name, lineno, full_name) 列表。"""
    imports = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imports.append((name, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imports.append((name, node.lineno, alias.name))
    return imports


def find_usage(source_lines, import_name, import_lineno):
    """在源码中检查 import_name 在非 import 行的引用。"""
    pattern = re.compile(rf"\b{re.escape(import_name)}\b")
    for i, line in enumerate(source_lines, start=1):
        if i == import_lineno:
            continue
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            continue
        if pattern.search(line):
            return True
    return False


def check_file(path):
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [skip] {path}: {e}")
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"  [syntax-error] {path}: {e}")
        return []

    source_lines = source.splitlines()
    unused = []
    for name, lineno, full_name in collect_imports(tree):
        if not find_usage(source_lines, name, lineno):
            unused.append((lineno, name, full_name))
    return unused


def main():
    if len(sys.argv) < 2:
        print("usage: python _check_unused_imports.py <dir-or-file> [more...]")
        sys.exit(1)

    targets = [Path(arg) for arg in sys.argv[1:]]
    py_files = []
    for t in targets:
        if t.is_dir():
            py_files.extend(t.rglob("*.py"))
        elif t.is_file() and t.suffix == ".py":
            py_files.append(t)

    py_files = sorted(set(py_files))

    total_unused = 0
    for f in py_files:
        unused = check_file(f)
        if unused:
            print(f"\n{f}")
            for lineno, name, full_name in unused:
                print(f"  line {lineno}: {full_name}  (as {name})")
                total_unused += 1
    print(f"\nTotal unused: {total_unused}")


if __name__ == "__main__":
    main()
