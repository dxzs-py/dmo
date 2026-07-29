from __future__ import annotations

import warnings

# 在 Django 包级别最早设置，消除 langgraph 的 allowed_objects 弃用警告
# 警告来源：langgraph/checkpoint/serde/jsonplus.py 第45行 LC_REVIVER = Reviver()
# 必须在该模块被导入前过滤，否则 Reviver() 无参构造时就会触发警告
warnings.filterwarnings(
    "ignore",
    message=".*allowed_objects.*",
    category=PendingDeprecationWarning,
)
# 同时过滤 LangChain 自定义的 PendingDeprecationWarning 子类
try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    warnings.filterwarnings(
        "ignore",
        message=".*allowed_objects.*",
        category=LangChainPendingDeprecationWarning,
    )
except ImportError:
    pass

try:
    from .celery import app as celery_app

    __all__ = ("celery_app",)
except ImportError:
    __all__ = ()
