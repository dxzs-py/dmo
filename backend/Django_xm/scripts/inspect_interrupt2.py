import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

import inspect

from langgraph.types import Interrupt

src = inspect.getsource(Interrupt)
print("=== Interrupt 完整源码 ===")
print(src)

# 查找 _DEFAULT_INTERRUPT_ID
import langgraph.types as lt

for name in dir(lt):
    if "DEFAULT" in name or "INTERRUPT" in name:
        print(f"\nlt.{name} = {getattr(lt, name)!r}")

# 检查 astream updates 中 __interrupt__ 的构造来源
print("\n=== 搜索 langgraph 中 __interrupt__ 构造 ===")
try:
    from langgraph import pregel

    src_p = inspect.getsource(pregel)
    import re

    for m in re.finditer(r"__interrupt__", src_p):
        start = max(0, m.start() - 200)
        end = min(len(src_p), m.end() + 200)
        print("---")
        print(src_p[start:end])
except Exception as e:
    print(f"pregel 检查失败: {e}")
