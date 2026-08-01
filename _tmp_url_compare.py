"""URL pattern comparison between baseline and current project."""
import re
from pathlib import Path

baseline_root = Path(r"D:\programming\langchain\langchain_xm (93)\langchain_xm\backend\Django_xm\Django_xm")
current_root = Path(r"d:\programming\langchain\langchain_xm\backend\Django_xm\Django_xm")


def extract_url_patterns(root: Path) -> dict[str, str]:
    """Extract URL patterns, return {url_path: name} mapping."""
    patterns = {}
    for urls_file in root.rglob("urls.py"):
        content = urls_file.read_text(encoding="utf-8")
        # Match path('...', ...) or path("...", ...)
        for match in re.finditer(r"""path\(\s*['"]([^'"]+)['"]\s*,.*?name\s*=\s*['"]([^'"]+)['"]""", content):
            url_path, name = match.group(1), match.group(2)
            patterns[name] = url_path
    return patterns


baseline = extract_url_patterns(baseline_root)
current = extract_url_patterns(current_root)

print(f"Baseline URL names: {len(baseline)}")
print(f"Current URL names: {len(current)}")
print()

# Find URLs in baseline but NOT in current (by name)
missing_names = set(baseline.keys()) - set(current.keys())
print(f"=== URL names in baseline but MISSING in current ({len(missing_names)}) ===")
for name in sorted(missing_names):
    print(f"  {name}: path='{baseline[name]}'")

print()

# Find URLs in current but NOT in baseline (by name)
new_names = set(current.keys()) - set(baseline.keys())
print(f"=== URL names in current but NOT in baseline ({len(new_names)}) ===")
for name in sorted(new_names):
    print(f"  {name}: path='{current[name]}'")

print()

# Find URLs where path changed for the same name
changed_paths = []
for name in set(baseline.keys()) & set(current.keys()):
    if baseline[name] != current[name]:
        changed_paths.append((name, baseline[name], current[name]))
print(f"=== URLs with CHANGED path ({len(changed_paths)}) ===")
for name, old, new in sorted(changed_paths):
    print(f"  {name}: '{old}' -> '{new}'")
