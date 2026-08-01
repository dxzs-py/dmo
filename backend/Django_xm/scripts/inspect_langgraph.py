"""检查 langgraph 版本与 astream updates 中 __interrupt__ 元素的实际类型/id。"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

import langgraph

print(f"langgraph __version__: {getattr(langgraph, '__version__', 'N/A')}")
import importlib.metadata as md

for pkg in ["langgraph", "langgraph-core", "langchain", "langchain-core"]:
    try:
        print(f"{pkg}=={md.version(pkg)}")
    except Exception:
        print(f"{pkg}: not found")

# 检查 Interrupt 在 updates stream 中的形态：查看 langgraph 源码中 __interrupt__ 如何构造
import inspect

from langgraph.types import Interrupt

src = inspect.getsource(Interrupt)
print("\n=== Interrupt 源码（前 60 行）===")
print("\n".join(src.splitlines()[:60]))
