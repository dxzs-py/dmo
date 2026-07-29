from datetime import timezone

"""批量修复 ruff DTZ005 和 DTZ006 错误。

DTZ005: timezone.now() 未指定时区
  - Django 文件: → timezone.now() (from django.utils import timezone)
  - 独立脚本: → datetime.now(timezone.utc) (from datetime import timezone)

DTZ006: datetime.fromtimestamp(ts, tz=timezone.utc) 未指定时区
  - → datetime.fromtimestamp(ts, tz=timezone.utc) (from datetime import timezone)
"""
import re
from pathlib import Path

# 不使用 Django 的独立脚本（使用 datetime.now(timezone.utc)）
STANDALONE_FILES = {
    "data/tools/skills/1/baidu-search/scripts/search.py",
}


def fix_django_file(content: str) -> str:
    """修复 Django 文件中的 DTZ005 和 DTZ006 错误。"""
    original = content

    # DTZ005: timezone.now() → timezone.now()
    # \b 确保不匹配 _datetime.now() 等；\s* 允许空格；不匹配带参数的调用
    content = re.sub(r"\bdatetime\.now\(\s*\)", "timezone.now()", content)

    # DTZ006: datetime.fromtimestamp(ts, tz=timezone.utc) → datetime.fromtimestamp(ts, tz=timezone.utc)
    # 不匹配已带 tz= 参数的调用（已有多参数）
    # 参数可能含嵌套括号（如 f.stat().st_mtime），用一层嵌套匹配
    def replace_fromtimestamp(match: re.Match) -> str:
        inner = match.group(1)
        # 检查顶层是否有逗号（表示已有多参数）
        depth = 0
        for c in inner:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 0:
                return match.group(0)  # 已有多参数，跳过
        return f"datetime.fromtimestamp({inner}, tz=timezone.utc)"

    content = re.sub(
        r"datetime\.fromtimestamp\(((?:[^()]|\([^()]*\))*)\)",
        replace_fromtimestamp,
        content,
    )

    if content == original:
        return content

    needs_django_timezone = "timezone.now()" in content
    needs_datetime_timezone = "tz=timezone.utc" in content

    lines = content.split("\n")

    # 添加 from django.utils import timezone（若尚未导入）
    if needs_django_timezone and "from django.utils import timezone" not in content:
        insert_pos = -1
        for i, line in enumerate(lines):
            if line.startswith("from django") or line.startswith("import django"):
                insert_pos = i + 1
            elif (line.startswith("from ") or line.startswith("import ")) and insert_pos == -1:
                insert_pos = i
                break
        if insert_pos == -1:
            # 没有任何 import，找到第一行非空非注释代码
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
                    insert_pos = i
                    break
        if insert_pos >= 0:
            lines.insert(insert_pos, "from django.utils import timezone")

    # 添加 timezone 到 from datetime import（用于 DTZ006）
    if needs_datetime_timezone:
        for i, line in enumerate(lines):
            if line.startswith("from datetime import "):
                imports_str = line[len("from datetime import ") :].strip()
                parts = [p.strip() for p in imports_str.split(",")]
                if "timezone" not in parts:
                    parts.append("timezone")
                    lines[i] = "from datetime import " + ", ".join(parts)
                break
        else:
            lines.insert(0, "from datetime import timezone")

    return "\n".join(lines)


def fix_standalone_file(content: str) -> str:
    """修复独立脚本中的 DTZ005 错误（使用 datetime.now(timezone.utc)）。"""
    original = content

    # DTZ005: timezone.now() → datetime.now(timezone.utc)
    content = re.sub(r"\bdatetime\.now\(\s*\)", "datetime.now(timezone.utc)", content)

    if content == original:
        return content

    # 确保 timezone 从 datetime 导入
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("from datetime import "):
            imports_str = line[len("from datetime import ") :].strip()
            parts = [p.strip() for p in imports_str.split(",")]
            if "timezone" not in parts:
                parts.append("timezone")
                lines[i] = "from datetime import " + ", ".join(parts)
            break

    return "\n".join(lines)


def main() -> None:
    backend_root = Path(".")
    fixed_count = 0

    for py_file in backend_root.rglob("*.py"):
        rel_path = py_file.as_posix()
        # 跳过缓存、迁移、虚拟环境等
        if any(
            seg in rel_path
            for seg in ("migrations", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".venv")
        ):
            continue

        try:
            with open(py_file, encoding="utf-8") as f:
                content = f.read()

            if rel_path in STANDALONE_FILES:
                new_content = fix_standalone_file(content)
            else:
                new_content = fix_django_file(content)

            if new_content != content:
                with open(py_file, "w", encoding="utf-8") as f:
                    f.write(new_content)
                fixed_count += 1
                print(f"Fixed: {py_file}")
        except Exception as e:
            print(f"Error fixing {py_file}: {e}")

    print(f"\nTotal files fixed: {fixed_count}")


if __name__ == "__main__":
    main()
