"""
安全节点包装器 - 为 LangGraph 节点添加 Guardrails
"""

from typing import Callable, Optional, Any, Dict
from functools import wraps

from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.ai_engine.guardrails import (
    InputValidator,
    OutputValidator,
    ContentFilter,
)
from .state import StudyFlowState

logger = get_logger(__name__)


def with_input_guardrails(
    node_func: Callable,
    validator: Optional[InputValidator] = None,
    input_field: str = "question",
    strict_mode: bool = False,
):
    if validator is None:
        validator = InputValidator(
            content_filter=ContentFilter(),
            strict_mode=strict_mode,
        )

    @wraps(node_func)
    def wrapped_node(state: StudyFlowState) -> StudyFlowState:
        logger.info(f"[Guardrails] 对节点 '{node_func.__name__}' 执行输入验证")

        input_content = state.get(input_field, "")

        if input_content:
            result = validator.validate(str(input_content))

            if not result.is_valid:
                error_msg = f"输入验证失败: {', '.join(result.errors)}"
                logger.error(f"[Guardrails] ❌ {error_msg}")

                state["error"] = error_msg
                state["validation_failed"] = True
                return state

            if result.warnings:
                logger.warning(f"[Guardrails] ⚠️ 输入警告: {result.warnings}")
                state["warnings"] = state.get("warnings", []) + result.warnings

            state[input_field] = result.filtered_input
            logger.info(f"[Guardrails] ✅ 输入验证通过")

        return node_func(state)

    return wrapped_node


def with_output_guardrails(
    node_func: Callable,
    validator: Optional[OutputValidator] = None,
    output_field: str = "plan",
    require_sources: bool = False,
    strict_mode: bool = False,
):
    if validator is None:
        validator = OutputValidator(
            content_filter=ContentFilter(),
            require_sources=require_sources,
            strict_mode=strict_mode,
        )

    @wraps(node_func)
    def wrapped_node(state: StudyFlowState) -> StudyFlowState:
        result_state = node_func(state)

        logger.info(f"[Guardrails] 对节点 '{node_func.__name__}' 执行输出验证")

        output_content = result_state.get(output_field, "")

        if output_content:
            sources = None
            if require_sources:
                sources = result_state.get("sources", []) or result_state.get("retrieved_docs", [])
                if sources and hasattr(sources[0], "metadata"):
                    sources = [
                        doc.metadata.get("source", "unknown")
                        for doc in sources
                        if hasattr(doc, "metadata")
                    ]

            validation_result = validator.validate(
                str(output_content),
                sources=sources,
            )

            if not validation_result.is_valid:
                error_msg = f"输出验证失败: {', '.join(validation_result.errors)}"
                logger.error(f"[Guardrails] ❌ {error_msg}")

                result_state["error"] = error_msg
                result_state["validation_failed"] = True
                return result_state

            if validation_result.warnings:
                logger.warning(f"[Guardrails] ⚠️ 输出警告: {validation_result.warnings}")
                result_state["warnings"] = result_state.get("warnings", []) + validation_result.warnings

            result_state[output_field] = validation_result.filtered_output
            logger.info(f"[Guardrails] ✅ 输出验证通过")

        return result_state

    return wrapped_node


def with_guardrails(
    input_field: Optional[str] = None,
    output_field: Optional[str] = None,
    require_sources: bool = False,
    strict_mode: bool = False,
):
    def decorator(node_func: Callable) -> Callable:
        wrapped = node_func

        if output_field:
            wrapped = with_output_guardrails(
                wrapped,
                output_field=output_field,
                require_sources=require_sources,
                strict_mode=strict_mode,
            )

        if input_field:
            wrapped = with_input_guardrails(
                wrapped,
                input_field=input_field,
                strict_mode=strict_mode,
            )

        return wrapped

    return decorator


def create_safe_node(
    node_func: Callable,
    validate_input: bool = True,
    validate_output: bool = True,
    input_field: str = "question",
    output_field: str = "result",
    require_sources: bool = False,
    strict_mode: bool = False,
) -> Callable:
    wrapped = node_func

    if validate_output:
        wrapped = with_output_guardrails(
            wrapped,
            output_field=output_field,
            require_sources=require_sources,
            strict_mode=strict_mode,
        )

    if validate_input:
        wrapped = with_input_guardrails(
            wrapped,
            input_field=input_field,
            strict_mode=strict_mode,
        )

    return wrapped


def add_guardrails_to_nodes(
    nodes_dict: Dict[str, Callable],
    config: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Callable]:
    config = config or {}
    safe_nodes = {}

    for node_name, node_func in nodes_dict.items():
        node_config = config.get(node_name, {})

        safe_nodes[node_name] = create_safe_node(
            node_func,
            **node_config,
        )

        logger.info(f"[Guardrails] 为节点 '{node_name}' 添加了安全检查")

    return safe_nodes


def create_human_review_node(
    review_field: str = "plan",
    approval_required: bool = True,
):
    def human_review_node(state: StudyFlowState) -> StudyFlowState:
        logger.info(f"[Human Review] 等待人工审核字段: {review_field}")

        content = state.get(review_field, "")

        logger.info(f"[Human Review] 审核内容: {content[:100]}...")

        state["human_review_approved"] = True
        state["human_review_content"] = content

        logger.info("[Human Review] 人工审核通过（演示模式）")

        return state

    return human_review_node