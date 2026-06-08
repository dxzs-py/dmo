#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import warnings


def _suppress_langgraph_allowed_objects_warning():
    """在所有 langgraph/langchain 导入前，过滤 allowed_objects 弃用警告。
    警告来源：langgraph/checkpoint/serde/jsonplus.py 第45行 LC_REVIVER = Reviver()
    必须在该模块被导入前过滤。"""
    warnings.filterwarnings(
        "ignore",
        message=".*allowed_objects.*",
        category=PendingDeprecationWarning,
    )
    try:
        from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
        warnings.filterwarnings(
            "ignore",
            message=".*allowed_objects.*",
            category=LangChainPendingDeprecationWarning,
        )
    except ImportError:
        pass


def main():
    """Run administrative tasks."""
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        # psycopg3 异步模式需要 SelectorEventLoop，Windows 默认 ProactorEventLoop 不兼容
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 在 Django 启动前设置，确保 langgraph 导入时不再触发警告
    _suppress_langgraph_allowed_objects_warning()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
