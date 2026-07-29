from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


# 延迟导入，避免循环依赖
def _get_registry_config() -> dict[str, Any]:
    from Django_xm.apps.ai_engine.services.registry_service import get_provider_config

    return get_provider_config("groq")


def get_provider_config() -> dict[str, Any]:
    return _get_registry_config().copy()


def patch_groq_model(model: BaseChatModel) -> BaseChatModel:
    """为 Groq 模型注入 bind_tools 补丁

    1. 如果模型缺少 bind_tools 方法，添加一个空实现
    2. 如果模型有 bind_tools，包装它以注入 parallel_tool_calls=False
    """
    if not hasattr(model, "bind_tools"):
        # 新版 ChatGroq 可能没有 bind_tools，添加空实现
        def _noop_bind_tools(tools, *, tool_choice=None, **kw):
            return model.bind(tools=tools, tool_choice=tool_choice, **kw)

        try:
            model.bind_tools = _noop_bind_tools
            logger.debug("Groq 模型已注入 bind_tools 空实现")
        except (AttributeError, TypeError):
            # Pydantic v2 不允许动态设置属性，用 __dict__ 绕过
            try:
                object.__setattr__(model, "bind_tools", _noop_bind_tools)
                logger.debug("Groq 模型已通过 __setattr__ 注入 bind_tools")
            except Exception:
                logger.warning("Groq 模型无法注入 bind_tools，tool calling 可能不可用")
        return model

    # 从类上获取原始 bind_tools 方法（避免 Pydantic v2 字段默认值 None 覆盖方法）
    original_bind = None
    for cls in type(model).__mro__:
        if "bind_tools" in cls.__dict__:
            candidate = cls.__dict__["bind_tools"]
            if callable(candidate):
                original_bind = candidate
                break

    if original_bind is None:
        # 回退：尝试从实例获取
        original_bind = getattr(model, "bind_tools", None)

    if original_bind is None or not callable(original_bind):
        # 完全没有可用的 bind_tools，用 bind() 替代
        def _groq_bind_tools(tools, *, tool_choice=None, **kw):
            kw.setdefault("parallel_tool_calls", False)
            return model.bind(tools=tools, tool_choice=tool_choice, **kw)
    else:

        def _groq_bind_tools(tools, *, tool_choice=None, **kw):
            kw.setdefault("parallel_tool_calls", False)
            return original_bind(model, tools, tool_choice=tool_choice, **kw)

    try:
        model.bind_tools = _groq_bind_tools
        logger.debug("Groq 模型已注入 parallel_tool_calls=False")
    except (AttributeError, TypeError):
        try:
            object.__setattr__(model, "bind_tools", _groq_bind_tools)
            logger.debug("Groq 模型已通过 __setattr__ 注入 parallel_tool_calls 补丁")
        except Exception:
            logger.warning("Groq 模型无法注入 bind_tools 补丁")

    return model


def is_groq_model(model: Any) -> bool:
    if isinstance(model, str):
        return "groq" in model.lower() or "llama" in model.lower()
    model_cls = type(model).__name__.lower()
    if "groq" in model_cls:
        return True
    model_name = getattr(model, "model_name", "") or getattr(model, "model", "") or ""
    return "llama" in str(model_name).lower()
