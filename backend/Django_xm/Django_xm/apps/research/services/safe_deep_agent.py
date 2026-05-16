"""
安全深度研究智能体 - 集成 Guardrails Middleware 的 DeepAgent

架构改进（v3）：
1. 使用 LangChain AgentMiddleware 替代外部 Runnable 管道验证
2. Guardrails 在 Agent ReAct 循环内部生效（wrap_model_call / wrap_tool_call）
3. middleware 实例正确传递给子智能体 create_agent 调用
4. 保留人工审核机制作为 before_model 钩子
5. 输出验证在 after_model 钩子中执行
"""

from typing import Optional, Dict, Any, List, Sequence, Callable

from langchain_core.tools import BaseTool
from langchain.agents.middleware import AgentMiddleware

from Django_xm.apps.config_center.config import get_logger
from Django_xm.apps.ai_engine.guardrails import (
    create_standard_guardrails,
    ResearchReport,
)
from Django_xm.apps.research.services.deep_agent import DeepResearchAgent, ResearchState

logger = get_logger(__name__)


class SafeDeepResearchAgent:
    def __init__(
        self,
        thread_id: str,
        enable_web_search: bool = True,
        enable_doc_analysis: bool = False,
        retriever_tool: Optional[BaseTool] = None,
        middleware: Optional[Sequence[AgentMiddleware]] = None,
        enable_input_validation: bool = True,
        enable_output_validation: bool = True,
        enable_human_review: bool = False,
        strict_mode: bool = False,
        checkpointer: Optional[Any] = None,
        human_review_callback: Optional[Callable[[str, str], bool]] = None,
        **kwargs,
    ):
        self.thread_id = thread_id
        self.enable_input_validation = enable_input_validation
        self.enable_output_validation = enable_output_validation
        self.enable_human_review = enable_human_review
        self.strict_mode = strict_mode
        self._human_review_callback = human_review_callback

        logger.info(f"初始化 SafeDeepResearchAgent: {thread_id}")
        logger.info(f"   输入验证: {enable_input_validation}")
        logger.info(f"   输出验证: {enable_output_validation}")
        logger.info(f"   人工审核: {enable_human_review}")
        logger.info(f"   严格模式: {strict_mode}")

        guardrails_stack = create_standard_guardrails(
            enable_input_validation=enable_input_validation,
            enable_output_validation=enable_output_validation,
            strict_mode=strict_mode,
            require_sources=True,
            validate_tool_calls=True,
            raise_on_error=True,
            extra_middleware=list(middleware) if middleware else None,
        )

        self.agent = DeepResearchAgent(
            thread_id=thread_id,
            enable_web_search=enable_web_search,
            enable_doc_analysis=enable_doc_analysis,
            retriever_tool=retriever_tool,
            checkpointer=checkpointer,
            enable_guardrails=True,
            middleware=guardrails_stack,
            **kwargs,
        )

        self.tool_calls_log: List[Dict[str, Any]] = []

        logger.info("SafeDeepResearchAgent 初始化完成")

    def research(
        self,
        query: str,
        return_structured: bool = True,
        callbacks: Optional[list] = None,
    ):
        logger.info(f"开始安全深度研究: {query[:50]}...")

        if self.enable_human_review:
            logger.info("等待人工审核研究问题...")
            approval = self._request_human_approval(
                action="开始研究",
                content=query,
            )

            if not approval:
                logger.warning("人工审核未通过，取消研究")
                raise ValueError("人工审核未通过")

        try:
            result = self.agent.research(query, callbacks=callbacks)
            final_report = result.get("final_report", "")

        except Exception as e:
            logger.error(f"研究执行失败: {e}")
            raise

        sources = self._extract_sources(result)

        logger.info("安全深度研究完成")

        if return_structured:
            return self._parse_to_structured_report(
                final_report,
                query,
                sources,
            )
        else:
            return {
                "final_report": final_report,
                "query": query,
                "sources": sources,
                "thread_id": self.thread_id,
            }

    async def aresearch(
        self,
        query: str,
        return_structured: bool = True,
    ):
        logger.info(f"异步开始安全深度研究: {query[:50]}...")

        if self.enable_human_review:
            logger.info("等待人工审核研究问题...")
            approval = self._request_human_approval(
                action="开始研究",
                content=query,
            )

            if not approval:
                logger.warning("人工审核未通过，取消研究")
                raise ValueError("人工审核未通过")

        try:
            result = await self.agent.aresearch(query)
            final_report = result.get("final_report", "")
        except Exception as e:
            logger.error(f"异步研究执行失败: {e}")
            raise

        sources = self._extract_sources(result)

        logger.info("异步安全深度研究完成")

        if return_structured:
            return self._parse_to_structured_report(
                final_report,
                query,
                sources,
            )
        else:
            return {
                "final_report": final_report,
                "query": query,
                "sources": sources,
                "thread_id": self.thread_id,
            }

    async def astream_research(self, query: str):
        logger.info(f"流式安全深度研究: {query[:50]}...")

        async for event in self.agent.astream_research(query):
            yield event

    def _request_human_approval(
        self,
        action: str,
        content: str,
    ) -> bool:
        logger.info(f"请求人工审核: {action}")
        logger.info(f"   内容: {content[:100]}...")

        if self._human_review_callback is not None:
            try:
                result = self._human_review_callback(action, content)
                logger.info(f"   人工审核回调结果: {'通过' if result else '拒绝'}")
                return bool(result)
            except Exception as e:
                logger.error(f"   人工审核回调异常: {e}")
                return False

        logger.info("   [自动批准 - 演示模式]")
        return True

    def _extract_sources(self, result: Dict[str, Any]) -> List[str]:
        sources = []

        if hasattr(self.agent, "filesystem"):
            fs = self.agent.filesystem

            try:
                sources_file = fs.read("sources.json")
                if sources_file:
                    import json
                    sources_data = json.loads(sources_file)
                    sources = sources_data.get("sources", [])
            except Exception as e:
                logger.warning(f"无法读取来源信息: {e}")

        if not sources:
            sources = ["网络搜索", "知识库"]

        return sources

    def _parse_to_structured_report(
        self,
        report_text: str,
        query: str,
        sources: List[str],
    ):
        from Django_xm.apps.ai_engine.guardrails import ResearchSection

        lines = report_text.split("\n")

        title = lines[0].strip() if lines else f"关于 {query} 的研究报告"

        summary_lines = []
        for line in lines[1:10]:
            if line.strip():
                summary_lines.append(line.strip())
        summary = " ".join(summary_lines) if summary_lines else report_text[:200]

        sections = [
            ResearchSection(
                section_number=1,
                title="研究内容",
                content=report_text,
                sources=sources,
                key_findings=["详见报告内容"],
            )
        ]

        conclusions = ["基于研究内容得出的结论"]

        return ResearchReport(
            title=title,
            topic=query,
            summary=summary,
            sections=sections,
            conclusions=conclusions,
            references=sources,
            created_at=datetime.now(),
            metadata={
                "thread_id": self.thread_id,
                "tool_calls_count": len(self.tool_calls_log),
            }
        )

    def get_tool_calls_log(self) -> List[Dict[str, Any]]:
        return self.tool_calls_log

    def get_middleware(self):
        return self.guardrails_middleware
