"""
Guardrails 中间件 - 基于 LangChain AgentMiddleware 实现

使用 LangChain v1.2+ 的原生 Middleware 机制，
在 Agent 的 ReAct 循环内部拦截模型调用和工具调用。

核心能力：
1. wrap_model_call: 拦截每次模型调用，验证输入和输出
2. wrap_tool_call: 拦截每次工具调用，使用 ToolCallRequest 模式验证参数和结果
3. before_model / after_model: 节点式钩子，状态检查和日志
4. before_agent / after_agent: Agent 生命周期钩子，状态管理和审计
5. awrap_*: 全异步支持

参考：
- https://docs.langchain.com/oss/python/langchain/middleware/custom
"""

from typing import Optional, Any, Dict, Callable, List

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ExtendedModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, BaseMessage, ToolMessage
from langgraph.types import Command
from langgraph.runtime import Runtime

from .input_validators import InputValidator
from .output_validators import OutputValidator
from .content_filters import ContentFilter
from Django_xm.apps.core.tool_permissions import READ_ONLY_TOOLS, WRITE_TOOLS, DANGEROUS_TOOLS

import logging
import time
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)


class RateLimitMiddleware(AgentMiddleware):
    """
    速率限制中间件

    防止 Agent 陷入无限工具调用循环或过快调用模型。
    - 限制单次 Agent 运行中模型调用和工具调用的最大次数
    - 限制调用频率（每秒最大调用数）
    - 超出限制时抛出异常终止 Agent 运行
    """

    def __init__(
        self,
        max_model_calls: int = 50,
        max_tool_calls: int = 30,
        max_calls_per_second: float = 5.0,
        max_total_calls: int = 80,
    ):
        super().__init__()
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_calls_per_second = max_calls_per_second
        self.max_total_calls = max_total_calls
        self._model_call_count = 0
        self._tool_call_count = 0
        self._call_timestamps: List[float] = []
        self._lock = threading.Lock()

    def _check_rate_limit(self, call_type: str) -> None:
        now = time.time()
        with self._lock:
            self._call_timestamps = [t for t in self._call_timestamps if now - t < 1.0]
            if len(self._call_timestamps) >= self.max_calls_per_second:
                raise RuntimeError(
                    f"速率限制: {call_type} 调用频率超过 {self.max_calls_per_second}/s，"
                    f"Agent 可能陷入循环，已终止执行。"
                )
            self._call_timestamps.append(now)

            total = self._model_call_count + self._tool_call_count
            if total >= self.max_total_calls:
                raise RuntimeError(
                    f"总量限制: Agent 总调用次数 ({total}) 超过 {self.max_total_calls}，"
                    f"可能陷入无限循环，已终止执行。"
                )

    def before_model(self, state: AgentState, runtime: Runtime) -> None:
        with self._lock:
            self._model_call_count += 1
        if self._model_call_count > self.max_model_calls:
            raise RuntimeError(
                f"模型调用次数 ({self._model_call_count}) 超过限制 ({self.max_model_calls})，"
                f"Agent 可能陷入循环，已终止执行。"
            )
        self._check_rate_limit("model")

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        with self._lock:
            self._tool_call_count += 1
        if self._tool_call_count > self.max_tool_calls:
            raise RuntimeError(
                f"工具调用次数 ({self._tool_call_count}) 超过限制 ({self.max_tool_calls})，"
                f"Agent 可能陷入循环，已终止执行。"
            )
        self._check_rate_limit("tool")
        return handler(tool_call)

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        with self._lock:
            self._tool_call_count += 1
        if self._tool_call_count > self.max_tool_calls:
            raise RuntimeError(
                f"工具调用次数 ({self._tool_call_count}) 超过限制 ({self.max_tool_calls})，"
                f"Agent 可能陷入循环，已终止执行。"
            )
        self._check_rate_limit("tool")
        return await handler(tool_call)

    def after_agent(self, state: AgentState, runtime: Runtime) -> None:
        logger.info(
            f"Agent 执行完成 - 模型调用: {self._model_call_count}, "
            f"工具调用: {self._tool_call_count}"
        )


def create_rate_limit_middleware(
    max_model_calls: int = 50,
    max_tool_calls: int = 30,
    max_calls_per_second: float = 5.0,
    max_total_calls: int = 80,
) -> RateLimitMiddleware:
    return RateLimitMiddleware(
        max_model_calls=max_model_calls,
        max_tool_calls=max_tool_calls,
        max_calls_per_second=max_calls_per_second,
        max_total_calls=max_total_calls,
    )


class GuardrailsMiddleware(AgentMiddleware):
    """
    基于 LangChain AgentMiddleware 的 Guardrails 中间件

    在 Agent 的 ReAct 循环内部生效：
    - wrap_model_call: 拦截每次模型调用，验证输入和输出
    - wrap_tool_call: 拦截每次工具调用，使用 ToolCallRequest 模式验证参数和结果
    - before_model / after_model: 节点式钩子，状态检查和日志
    - before_agent / after_agent: Agent 生命周期钩子
    """

    DANGEROUS_TOOLS = frozenset({
        "delete_file", "rm", "execute_code", "shell_exec",
        "bash_execute", "repl_execute", "notebook_edit",
    })

    def __init__(
        self,
        input_validator: Optional[InputValidator] = None,
        output_validator: Optional[OutputValidator] = None,
        on_input_error: Optional[Callable] = None,
        on_output_error: Optional[Callable] = None,
        raise_on_error: bool = True,
        validate_tool_calls: bool = True,
        max_message_count: int = 100,
        blocked_tools: Optional[set] = None,
    ):
        super().__init__()
        self.input_validator = input_validator or InputValidator()
        self.output_validator = output_validator or OutputValidator()
        self.on_input_error = on_input_error
        self.on_output_error = on_output_error
        self.raise_on_error = raise_on_error
        self.validate_tool_calls = validate_tool_calls
        self.max_message_count = max_message_count
        self.blocked_tools = self.DANGEROUS_TOOLS | (blocked_tools or set())
        self._agent_start_time: Optional[float] = None
        self._tool_call_count: int = 0
        self._model_call_count: int = 0

    def before_agent(self, state: AgentState, runtime: Runtime) -> Dict[str, Any] | None:
        self._agent_start_time = time.time()
        self._tool_call_count = 0
        self._model_call_count = 0
        query = ""
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                query = msg.content[:100]
                break
        logger.info(f"[Guardrails] Agent 开始执行, 查询: {query}...")
        return None

    def after_agent(self, state: AgentState, runtime: Runtime) -> Dict[str, Any] | None:
        duration = time.time() - self._agent_start_time if self._agent_start_time else 0
        logger.info(
            f"[Guardrails] Agent 执行完成, 耗时: {duration:.2f}s, "
            f"模型调用: {self._model_call_count} 次, 工具调用: {self._tool_call_count} 次"
        )
        self._agent_start_time = None
        return None

    def before_model(self, state: AgentState, runtime: Runtime) -> Dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) > self.max_message_count:
            logger.warning(
                f"消息数量超过 {self.max_message_count} 条，可能影响性能"
            )
        return None

    def after_model(self, state: AgentState, runtime: Runtime) -> Dict[str, Any] | None:
        self._model_call_count += 1
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage) and last_msg.content:
                logger.debug(f"模型输出长度: {len(last_msg.content)} 字符")
        return None

    def _validate_input(self, text: str, context: str = "输入") -> None:
        validation_result = self.input_validator.validate(text)
        if not validation_result.is_valid:
            logger.warning(f"{context}验证失败: {validation_result.errors}")
            if self.on_input_error:
                self.on_input_error(text, validation_result)
            if self.raise_on_error:
                raise ValueError(f"{context}验证失败: {', '.join(validation_result.errors)}")

    def _validate_output(self, text: str, context: str = "输出") -> None:
        validation_result = self.output_validator.validate(text)
        if not validation_result.is_valid:
            logger.warning(f"{context}验证失败: {validation_result.errors}")
            if self.on_output_error:
                self.on_output_error(text, validation_result)
            if self.raise_on_error:
                raise ValueError(f"{context}验证失败: {', '.join(validation_result.errors)}")

    def _extract_response_text(self, response: ModelResponse | ExtendedModelResponse) -> str:
        if hasattr(response, 'message') and response.message:
            return response.message.content if hasattr(response.message, 'content') else str(response.message)
        if hasattr(response, 'output') and response.output:
            return str(response.output)
        return ""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                self._validate_input(last_user_msg, "模型输入")

        response = handler(request)

        response_text = self._extract_response_text(response)
        if response_text:
            self._validate_output(response_text, "模型输出")

        return response

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if not self.validate_tool_calls:
            return handler(tool_call)

        tool_name = tool_call.tool_call.get("name", "")
        tool_args = tool_call.tool_call.get("args", {})

        if tool_name in self.blocked_tools:
            logger.warning(f"检测到危险工具调用: {tool_name}")
            if self.raise_on_error:
                raise ValueError(f"工具 {tool_name} 被安全策略禁止")
            return ToolMessage(
                content=f"工具 {tool_name} 被安全策略禁止",
                tool_call_id=tool_call.tool_call.get("id", ""),
            )

        for key, value in tool_args.items():
            if isinstance(value, str):
                validation_result = self.input_validator.validate(value)
                if not validation_result.is_valid:
                    logger.warning(f"工具参数验证失败 ({tool_name}.{key}): {validation_result.errors}")
                    if self.raise_on_error:
                        raise ValueError(f"工具参数验证失败: {', '.join(validation_result.errors)}")

        self._tool_call_count += 1
        result = handler(tool_call)

        output_text = ""
        if isinstance(result, ToolMessage):
            output_text = str(result.content) if result.content else ""
        elif isinstance(result, str):
            output_text = result

        if output_text:
            output_result = self.output_validator.validate(output_text)
            if not output_result.is_valid:
                logger.warning(f"工具输出验证失败 ({tool_name}): {output_result.errors}")
                if self.raise_on_error:
                    raise ValueError(f"工具输出验证失败: {', '.join(output_result.errors)}")

        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                self._validate_input(last_user_msg, "模型输入(异步)")

        response = await handler(request)

        response_text = self._extract_response_text(response)
        if response_text:
            self._validate_output(response_text, "模型输出(异步)")

        return response

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if not self.validate_tool_calls:
            return await handler(tool_call)

        tool_name = tool_call.tool_call.get("name", "")
        tool_args = tool_call.tool_call.get("args", {})

        if tool_name in self.blocked_tools:
            logger.warning(f"检测到危险工具调用: {tool_name}")
            if self.raise_on_error:
                raise ValueError(f"工具 {tool_name} 被安全策略禁止")
            return ToolMessage(
                content=f"工具 {tool_name} 被安全策略禁止",
                tool_call_id=tool_call.tool_call.get("id", ""),
            )

        for key, value in tool_args.items():
            if isinstance(value, str):
                validation_result = self.input_validator.validate(value)
                if not validation_result.is_valid:
                    logger.warning(f"工具参数验证失败 ({tool_name}.{key}): {validation_result.errors}")
                    if self.raise_on_error:
                        raise ValueError(f"工具参数验证失败: {', '.join(validation_result.errors)}")

        self._tool_call_count += 1
        result = await handler(tool_call)

        output_text = ""
        if isinstance(result, ToolMessage):
            output_text = str(result.content) if result.content else ""
        elif isinstance(result, str):
            output_text = result

        if output_text:
            output_result = self.output_validator.validate(output_text)
            if not output_result.is_valid:
                logger.warning(f"工具输出验证失败 ({tool_name}): {output_result.errors}")
                if self.raise_on_error:
                    raise ValueError(f"工具输出验证失败: {', '.join(output_result.errors)}")

        return result


class PIIMiddleware(AgentMiddleware):
    """
    PII（个人身份信息）检测与脱敏中间件

    在模型调用前后检测 PII，自动脱敏或拒绝包含敏感信息的请求。
    """

    def __init__(
        self,
        mask_pii: bool = True,
        reject_on_pii: bool = False,
        pii_patterns: Optional[Dict[str, str]] = None,
    ):
        super().__init__()
        self.mask_pii = mask_pii
        self.reject_on_pii = reject_on_pii
        self.pii_patterns = pii_patterns or {}
        self._content_filter = ContentFilter(
            enable_pii_detection=True,
            enable_content_safety=False,
            enable_injection_detection=False,
            mask_pii=mask_pii,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                filter_result = self._content_filter.filter_input(last_user_msg)
                if not filter_result.is_safe:
                    if self.reject_on_pii:
                        raise ValueError("输入包含个人身份信息(PII)，已被安全策略拒绝")
                    logger.warning(f"检测到 PII: {filter_result.issues}")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                filter_result = self._content_filter.filter_input(last_user_msg)
                if not filter_result.is_safe:
                    if self.reject_on_pii:
                        raise ValueError("输入包含个人身份信息(PII)，已被安全策略拒绝")
                    logger.warning(f"检测到 PII: {filter_result.issues}")
        return await handler(request)


class HumanInTheLoopMiddleware(AgentMiddleware):
    """
    人工确认中间件

    对指定工具调用暂停执行，等待人工确认后继续。
    支持同步和异步模式。
    """

    def __init__(
        self,
        tools_requiring_approval: Optional[set] = None,
        auto_approve_timeout: float = 300.0,
        on_approval_request: Optional[Callable] = None,
    ):
        super().__init__()
        self.tools_requiring_approval = tools_requiring_approval or {
            "fs_write_file", "bash_execute", "repl_execute",
            "notebook_edit", "shell_exec", "execute_code",
        }
        self.auto_approve_timeout = auto_approve_timeout
        self.on_approval_request = on_approval_request

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if tool_call.tool_call.get("name", "") not in self.tools_requiring_approval:
            return handler(tool_call)

        if self.on_approval_request:
            approved = self.on_approval_request(
                tool_call.tool_call.get("name", ""),
                tool_call.tool_call.get("args", {}),
            )
            if not approved:
                logger.info(f"工具 {tool_call.tool_call.get('name', '')} 被人工拒绝")
                return ToolMessage(
                    content=f"工具 {tool_call.tool_call.get('name', '')} 的执行已被人工拒绝",
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )

        return handler(tool_call)

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if tool_call.tool_call.get("name", "") not in self.tools_requiring_approval:
            return await handler(tool_call)

        if self.on_approval_request:
            approved = self.on_approval_request(
                tool_call.tool_call.get("name", ""),
                tool_call.tool_call.get("args", {}),
            )
            if not approved:
                logger.info(f"工具 {tool_call.tool_call.get('name', '')} 被人工拒绝(异步)")
                return ToolMessage(
                    content=f"工具 {tool_call.tool_call.get('name', '')} 的执行已被人工拒绝",
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )

        return await handler(tool_call)


class SkillsMiddleware(AgentMiddleware):
    """
    技能中间件 - 为 Agent 提供可组合的技能注册与调度

    在工具调用前匹配已注册的技能模式，如果匹配则执行技能逻辑，
    否则传递给原始 handler。
    """

    def __init__(
        self,
        skills: Optional[Dict[str, Callable]] = None,
        auto_detect: bool = True,
    ):
        super().__init__()
        self.skills = skills or {}
        self.auto_detect = auto_detect

    def register_skill(self, name: str, handler: Callable) -> None:
        self.skills[name] = handler

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = tool_call.tool_call.get("name", "")
        skill = self.skills.get(tool_name)
        if skill:
            logger.info(f"[Skills] 使用技能 {tool_name} 处理工具调用")
            try:
                result = skill(tool_call.tool_call.get("args", {}))
                return ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )
            except Exception as e:
                logger.error(f"[Skills] 技能 {tool_name} 执行失败: {e}")
                return ToolMessage(
                    content=f"技能执行失败: {e}",
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )
        return handler(tool_call)

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = tool_call.tool_call.get("name", "")
        skill = self.skills.get(tool_name)
        if skill:
            logger.info(f"[Skills] 使用技能 {tool_name} 处理工具调用(异步)")
            try:
                import asyncio
                if asyncio.iscoroutinefunction(skill):
                    result = await skill(tool_call.tool_call.get("args", {}))
                else:
                    result = skill(tool_call.tool_call.get("args", {}))
                return ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )
            except Exception as e:
                logger.error(f"[Skills] 技能 {tool_name} 执行失败: {e}")
                return ToolMessage(
                    content=f"技能执行失败: {e}",
                    tool_call_id=tool_call.tool_call.get("id", ""),
                )
        return await handler(tool_call)


def create_guardrails_middleware(
    input_validator: Optional[InputValidator] = None,
    output_validator: Optional[OutputValidator] = None,
    strict_mode: bool = False,
    validate_tool_calls: bool = True,
    raise_on_error: bool = True,
    blocked_tools: Optional[set] = None,
) -> GuardrailsMiddleware:
    content_filter = ContentFilter(
        enable_pii_detection=True,
        enable_content_safety=True,
        enable_injection_detection=True,
        mask_pii=True,
    )

    return GuardrailsMiddleware(
        input_validator=input_validator or InputValidator(
            content_filter=content_filter,
            strict_mode=strict_mode,
        ),
        output_validator=output_validator or OutputValidator(
            content_filter=content_filter,
            require_sources=False,
            strict_mode=strict_mode,
        ),
        validate_tool_calls=validate_tool_calls,
        raise_on_error=raise_on_error,
        blocked_tools=blocked_tools,
    )


def create_pii_middleware(
    mask_pii: bool = True,
    reject_on_pii: bool = False,
) -> PIIMiddleware:
    return PIIMiddleware(mask_pii=mask_pii, reject_on_pii=reject_on_pii)


def create_human_in_the_loop_middleware(
    tools_requiring_approval: Optional[set] = None,
    on_approval_request: Optional[Callable] = None,
) -> HumanInTheLoopMiddleware:
    return HumanInTheLoopMiddleware(
        tools_requiring_approval=tools_requiring_approval,
        on_approval_request=on_approval_request,
    )


def create_skills_middleware(
    skills: Optional[Dict[str, Callable]] = None,
) -> SkillsMiddleware:
    return SkillsMiddleware(skills=skills)


def build_middleware_stack(
    enable_guardrails: bool = True,
    enable_pii: bool = False,
    enable_human_in_loop: bool = False,
    enable_skills: bool = False,
    enable_rate_limit: bool = True,
    guardrails_strict: bool = False,
    pii_reject: bool = False,
    skills_map: Optional[Dict[str, Callable]] = None,
    approval_tools: Optional[set] = None,
    on_approval_request: Optional[Callable] = None,
    extra_middleware: Optional[List[AgentMiddleware]] = None,
    max_model_calls: int = 50,
    max_tool_calls: int = 30,
) -> List[AgentMiddleware]:
    """
    构建 Middleware 栈，按优先级排列：
    RateLimitMiddleware -> PIIMiddleware -> GuardrailsMiddleware -> HumanInTheLoopMiddleware -> SkillsMiddleware
    """
    stack: List[AgentMiddleware] = []

    if enable_rate_limit:
        stack.append(create_rate_limit_middleware(
            max_model_calls=max_model_calls,
            max_tool_calls=max_tool_calls,
        ))

    if enable_pii:
        stack.append(create_pii_middleware(reject_on_pii=pii_reject))

    if enable_guardrails:
        stack.append(create_guardrails_middleware(
            strict_mode=guardrails_strict,
            raise_on_error=guardrails_strict,
        ))

    if enable_human_in_loop:
        stack.append(create_human_in_the_loop_middleware(
            tools_requiring_approval=approval_tools,
            on_approval_request=on_approval_request,
        ))

    if enable_skills and skills_map:
        stack.append(create_skills_middleware(skills=skills_map))

    if extra_middleware:
        stack.extend(extra_middleware)

    return stack


def create_guardrails_runnable(
    runnable,
    input_validator: Optional[InputValidator] = None,
    output_validator: Optional[OutputValidator] = None,
    validate_input: bool = True,
    validate_output: bool = True,
    raise_on_error: bool = True,
):
    from langchain_core.runnables import RunnableLambda

    middleware = GuardrailsMiddleware(
        input_validator=input_validator,
        output_validator=output_validator,
        raise_on_error=raise_on_error,
    )

    components = []

    if validate_input:
        components.append(
            RunnableLambda(middleware.validate_input).with_config(
                {"run_name": "input_validation"}
            )
        )

    components.append(runnable)

    if validate_output:
        components.append(
            RunnableLambda(middleware.validate_output).with_config(
                {"run_name": "output_validation"}
            )
        )

    if len(components) == 1:
        return components[0]

    result = components[0]
    for component in components[1:]:
        result = result | component

    return result


def create_input_filter(
    content_filter: Optional[ContentFilter] = None,
    strict_mode: bool = False,
) -> "RunnableLambda":
    from langchain_core.runnables import RunnableLambda

    validator = InputValidator(
        content_filter=content_filter,
        strict_mode=strict_mode,
    )

    def filter_func(input_data: Any) -> Any:
        text = input_data if isinstance(input_data, str) else str(input_data)
        result = validator.validate(text)
        if not result.is_valid:
            raise ValueError(f"输入验证失败: {', '.join(result.errors)}")
        return result.filtered_input

    return RunnableLambda(filter_func).with_config({"run_name": "input_filter"})


def create_output_filter(
    content_filter: Optional[ContentFilter] = None,
    require_sources: bool = False,
    strict_mode: bool = False,
) -> "RunnableLambda":
    from langchain_core.runnables import RunnableLambda

    validator = OutputValidator(
        content_filter=content_filter,
        require_sources=require_sources,
        strict_mode=strict_mode,
    )

    def filter_func(output_data: Any) -> Any:
        text = output_data if isinstance(output_data, str) else str(output_data)
        result = validator.validate(text)
        if not result.is_valid:
            raise ValueError(f"输出验证失败: {', '.join(result.errors)}")
        return result.filtered_output

    return RunnableLambda(filter_func).with_config({"run_name": "output_filter"})


def add_guardrails_to_agent(
    agent,
    enable_input_validation: bool = True,
    enable_output_validation: bool = True,
    strict_mode: bool = False,
):
    return create_guardrails_runnable(
        agent,
        input_validator=InputValidator(strict_mode=strict_mode) if enable_input_validation else None,
        output_validator=OutputValidator(strict_mode=strict_mode) if enable_output_validation else None,
        validate_input=enable_input_validation,
        validate_output=enable_output_validation,
        raise_on_error=True,
    )


class PermissionMiddleware(AgentMiddleware):
    """
    动态工具权限中间件

    使用官方 wrap_model_call 模式，在运行时根据用户权限动态过滤可用工具集。
    替代静态的 PermissionService.wrap_tools_with_permission 方式，
    支持运行时权限变更（如角色切换、权限回收）。

    参考：
    - https://docs.langchain.com/oss/python/langchain/middleware/custom
    """

    def __init__(
        self,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        allowed_tools: Optional[set] = None,
        blocked_tools: Optional[set] = None,
    ):
        super().__init__()
        self.user_id = user_id
        self.session_id = session_id
        self._allowed_tools = allowed_tools
        self._blocked_tools = blocked_tools or set()

    def _resolve_allowed_tools(self) -> Optional[set]:
        if self._allowed_tools is not None:
            return self._allowed_tools

        if self.user_id is None:
            return None

        try:
            from Django_xm.apps.core.permissions import PermissionService
            return PermissionService.get_allowed_tool_names(self.user_id)
        except Exception as e:
            logger.warning(f"获取用户权限失败: {e}")
            return None

    def _filter_tools(self, tools, allowed: set) -> list:
        KNOWN_TOOLS = READ_ONLY_TOOLS | WRITE_TOOLS | DANGEROUS_TOOLS
        filtered = []
        for t in tools:
            if t.name in self._blocked_tools:
                continue
            if t.name in allowed:
                filtered.append(t)
            elif t.name not in KNOWN_TOOLS:
                filtered.append(t)
        removed = {t.name for t in tools} - {t.name for t in filtered}
        if removed:
            logger.info(f"[Permission] 过滤工具: 移除 {removed} (user={self.user_id})")
        return filtered

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        tools = getattr(request, 'tools', None)
        if tools is None:
            return handler(request)

        allowed = self._resolve_allowed_tools()
        if allowed is not None:
            filtered = self._filter_tools(tools, allowed)
            request = request.override(tools=filtered) if hasattr(request, 'override') else request

        return handler(request)

    async def _aresolve_allowed_tools(self) -> Optional[set]:
        if self._allowed_tools is not None:
            return self._allowed_tools

        if self.user_id is None:
            return None

        try:
            from Django_xm.apps.core.permissions import PermissionService
            return await PermissionService.aget_allowed_tool_names(self.user_id, self.session_id)
        except Exception as e:
            logger.warning(f"异步获取用户权限失败: {e}")
            return None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        tools = getattr(request, 'tools', None)
        if tools is None:
            return await handler(request)

        allowed = await self._aresolve_allowed_tools()
        if allowed is not None:
            filtered = self._filter_tools(tools, allowed)
            request = request.override(tools=filtered) if hasattr(request, 'override') else request

        return await handler(request)


def create_permission_middleware(
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    allowed_tools: Optional[set] = None,
    blocked_tools: Optional[set] = None,
) -> PermissionMiddleware:
    return PermissionMiddleware(
        user_id=user_id,
        session_id=session_id,
        allowed_tools=allowed_tools,
        blocked_tools=blocked_tools,
    )


class GroqToolCallCompatMiddleware(AgentMiddleware):
    """
    Groq 模型工具调用兼容性中间件

    Groq 的 llama 模型偶尔生成格式不规范的 tool call（将工具名和参数拼成一个字符串），
    导致 Groq API 返回 tool call validation 错误。

    本中间件在模型调用失败时，自动重试不带工具的纯对话模式，
    并在响应中附加提示信息告知用户工具不可用。
    """

    PROVIDER_KEYWORDS = ("groq", "llama")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        tools = getattr(request, 'tools', None)
        if not tools:
            return await handler(request)

        model = getattr(request, 'model', None)
        model_name = ""
        if model:
            model_name = getattr(model, 'model_name', '') or getattr(model, 'model', '') or ""
            model_name = str(model_name).lower()

        is_groq = any(kw in model_name for kw in self.PROVIDER_KEYWORDS)
        if not is_groq and not isinstance(model, type(None)):
            model_cls = type(model).__name__.lower()
            is_groq = 'groq' in model_cls

        if not is_groq:
            return await handler(request)

        try:
            return await handler(request)
        except Exception as e:
            err_msg = str(e).lower()
            is_tool_validation = (
                'tool call validation' in err_msg
                or 'not in request.tools' in err_msg
                or 'tool_call_validation' in err_msg
            )
            if not is_tool_validation:
                raise

            logger.warning(
                f"[GroqCompat] Groq tool call 验证失败，降级为无工具模式: {e}"
            )
            no_tool_request = request.override(tools=[]) if hasattr(request, 'override') else request
            response = await handler(no_tool_request)

            if hasattr(response, 'messages') and response.messages:
                last_msg = response.messages[-1]
                if isinstance(last_msg, AIMessage) and last_msg.content:
                    notice = "\n\n> ⚠️ 当前模型暂不支持工具调用，已切换为纯对话模式。"
                    patched = AIMessage(
                        content=last_msg.content + notice,
                        id=getattr(last_msg, 'id', None),
                    )
                    response.messages[-1] = patched
            return response
