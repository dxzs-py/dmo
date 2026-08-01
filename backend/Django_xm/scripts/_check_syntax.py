"""Check all Python files for syntax errors."""

import ast
import os
import sys

errors = []
root = "Django_xm"
for dirpath, _, filenames in os.walk(root):
    for fname in filenames:
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(dirpath, fname)
        # Skip migrations and __pycache__
        norm = fpath.replace("\\", "/")
        if "/migrations/" in norm or "/__pycache__/" in norm:
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                ast.parse(f.read(), filename=fpath)
        except SyntaxError as e:
            errors.append(f"{fpath}:{e.lineno}: {e.msg}")

if errors:
    print(f"Found {len(errors)} syntax errors:")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("No syntax errors found.")
