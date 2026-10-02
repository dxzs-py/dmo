"""learning 服务层出口。

模块级惰性导出（PEP 562）：study_flow/workflow_service 在导入时会加载
nodes 包（study_flow.py 需要 planner/grading 等节点构建图），而 nodes 内部
又反向引用 services._model_helper——若本包在 nodes 初始化期间被急切加载
（如全量测试按字母序先 import nodes），会形成循环导入，使节点函数被绑定
为部分初始化的模块对象（StudyFlow._build_graph 报 TypeError）。

因此 study_flow / workflow_service 相关符号走 __getattr__ 惰性加载；
persistence_service / resilience 不依赖 nodes，保持急切导入。
"""

from .persistence_service import WorkflowPersistenceService, get_persistence_service
from .resilience import (
    ainvoke_with_resilience,
    astream_with_resilience,
    invoke_with_resilience,
    stream_with_resilience,
)

__all__ = [
    "WorkflowPersistenceService",
    "WorkflowService",
    "_get_study_flow",
    "ainvoke_with_resilience",
    "astream_with_resilience",
    "get_persistence_service",
    "invoke_with_resilience",
    "stream_with_resilience",
]

# 触发 nodes 导入链的符号 → (子模块, 属性)；首次访问后缓存到模块 globals
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "_get_study_flow": (".study_flow", "_get_study_flow"),
    "WorkflowService": (".workflow_service", "WorkflowService"),
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        from importlib import import_module

        module_path, attr = _LAZY_EXPORTS[name]
        value = getattr(import_module(module_path, __name__), attr)
        globals()[name] = value  # 缓存，后续访问不再走 __getattr__
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
