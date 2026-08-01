"""临时 ruff 统计脚本：按规则码统计问题数。"""
import subprocess
import collections
import re
import sys

result = subprocess.run(
    ["ruff", "check", r"d:\programming\langchain\langchain_xm\backend\Django_xm",
     "--output-format=concise"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)

# 格式: path:line:col: CODE message
pattern = re.compile(r":\s+([A-Z]+\d+)\s")
counter = collections.Counter()
for line in result.stdout.splitlines():
    m = pattern.search(line)
    if m:
        counter[m.group(1)] += 1

total = sum(counter.values())
print(f"=== Ruff 统计 (总计 {total} 个问题) ===")
for rule, n in counter.most_common(40):
    print(f"{n:5d}  {rule}")
