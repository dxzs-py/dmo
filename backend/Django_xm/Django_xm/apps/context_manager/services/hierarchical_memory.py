"""
分层记忆系统

按优先级从高到低组织上下文，各层独立 Token 预算：

L1: OrganizationPolicy  — 组织级策略（全局，SystemConfig key=organization_policy）
L2: UserGlobalRules     — 用户全局规则（ContextRule scope=user_global）
L3: ProjectRules        — 项目规则（ContextRule scope=project，按 project_id 过滤）
L4: ConversationHistory — 对话历史（委托给 ProgressiveCompressor，不在 load_context 中处理）
L5: VectorRetrieval     — 向量检索记忆（委托给 ContextKnowledgeGraph，不在 load_context 中处理）
L6: AutoMemory          — 自动记忆（AutoMemory 模型，限制 200 行 / 25KB）

load_context 只返回 L1+L2+L3+L6 的拼接结果，L4/L5 由中间件独立管理。
"""

import asyncio
from typing import ClassVar

from asgiref.sync import sync_to_async

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────
_AUTO_MEMORY_MAX_ROWS = 200
_AUTO_MEMORY_MAX_BYTES = 25 * 1024  # 25 KB


def _is_async_context() -> bool:
    """检测当前是否在异步事件循环中"""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


# ── 同步数据库查询（供 sync_to_async 包装） ───────────────────────────


def _fetch_organization_policy() -> str:
    """L1: 从 SystemConfig 读取 key=organization_policy"""
    from Django_xm.apps.ai_engine.models import SystemConfig

    value = SystemConfig.get_value("organization_policy", default=None)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # JSONField 可能存为 dict/list，序列化为字符串
    import json

    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _fetch_user_global_rules(user_id) -> str:
    """L2: 读取用户全局规则（scope=user_global）"""
    from Django_xm.apps.context_manager.models import ContextRule

    rules = list(
        ContextRule.objects.filter(
            user_id=user_id,
            scope="user_global",
            is_active=True,
        ).order_by("-priority", "-updated_at")
    )
    if not rules:
        return ""
    parts = []
    for rule in rules:
        parts.append(f"# {rule.name}\n{rule.content}")
    return "\n\n".join(parts)


def _fetch_project_rules(user_id, project_id) -> str:
    """L3: 读取项目规则（scope=project，按 project_id 过滤）"""
    if not project_id:
        return ""
    from Django_xm.apps.context_manager.models import ContextRule

    rules = list(
        ContextRule.objects.filter(
            user_id=user_id,
            scope="project",
            project_id=project_id,
            is_active=True,
        ).order_by("-priority", "-updated_at")
    )
    if not rules:
        return ""
    parts = []
    for rule in rules:
        parts.append(f"# {rule.name}\n{rule.content}")
    return "\n\n".join(parts)


def _fetch_auto_memory(user_id, project_id) -> str:
    """L6: 读取自动记忆（前 200 行，总计不超过 25KB）"""
    from Django_xm.apps.context_manager.models import AutoMemory

    qs = AutoMemory.objects.filter(user_id=user_id)
    if project_id:
        qs = qs.filter(project_id=project_id)
    qs = qs.order_by("-last_accessed_at")[:_AUTO_MEMORY_MAX_ROWS]

    memories = list(qs)
    if not memories:
        return ""

    lines = []
    total_bytes = 0
    for mem in memories:
        line = f"- [{mem.source}] {mem.content}"
        line_bytes = len(line.encode("utf-8"))
        if total_bytes + line_bytes > _AUTO_MEMORY_MAX_BYTES:
            break
        lines.append(line)
        total_bytes += line_bytes

    return "\n".join(lines)


def _save_auto_memory_sync(user_id, project_id, content, source, tags) -> bool:
    """同步保存自动记忆"""
    from Django_xm.apps.context_manager.models import AutoMemory

    try:
        AutoMemory.objects.create(
            user_id=user_id,
            project_id=project_id or "",
            content=content,
            source=source or "other",
            relevance_tags=tags or [],
        )
        return True
    except Exception:
        logger.exception("保存自动记忆失败: user=%s, project=%s", user_id, project_id)
        return False


def _trim_auto_memory_sync(user_id, project_id) -> None:
    """清理超出限制的自动记忆（LRU 淘汰）"""
    from Django_xm.apps.context_manager.models import AutoMemory

    try:
        qs = AutoMemory.objects.filter(user_id=user_id)
        if project_id:
            qs = qs.filter(project_id=project_id)

        total = qs.count()
        if total <= _AUTO_MEMORY_MAX_ROWS:
            # 检查总大小
            all_memories = list(qs.order_by("-last_accessed_at"))
            total_bytes = sum(len(m.content.encode("utf-8")) for m in all_memories)
            if total_bytes <= _AUTO_MEMORY_MAX_BYTES:
                return
            # 按大小淘汰：从最久未访问的开始删除
            for mem in reversed(all_memories):
                total_bytes -= len(mem.content.encode("utf-8"))
                mem.delete()
                if total_bytes <= _AUTO_MEMORY_MAX_BYTES:
                    break
        else:
            # 按行数淘汰
            keep_ids = list(qs.order_by("-last_accessed_at").values_list("id", flat=True)[:_AUTO_MEMORY_MAX_ROWS])
            qs.exclude(id__in=keep_ids).delete()
    except Exception:
        logger.exception("清理自动记忆失败: user=%s, project=%s", user_id, project_id)


# ── 主类 ──────────────────────────────────────────────────────────────


class HierarchicalMemory:
    """分层记忆系统

    按层级组织上下文，各层独立 Token 预算，用 XML 标签分隔。
    load_context 只返回 L1+L2+L3+L6 的拼接结果。
    L4（对话历史）和 L5（向量检索）由中间件独立管理。
    """

    # 各层 Token 预算默认值
    _DEFAULT_BUDGETS: ClassVar[dict[str, int]] = {
        "memory_l1_budget": 500,
        "memory_l2_budget": 800,
        "memory_l3_budget": 800,
        "memory_l4_budget": 4000,
        "memory_l5_budget": 1000,
        "memory_l6_budget": 500,
    }

    def __init__(
        self,
        user_id,
        project_id=None,
        model_name="",
        store=None,
        thread_id=None,
    ):
        self._user_id = user_id
        self._project_id = project_id
        self._model_name = model_name
        self._store = store
        self._thread_id = thread_id

        # 委托实例（延迟初始化）
        self._compressor = None
        self._knowledge_graph = None

    # ── 预算读取 ──────────────────────────────────────────────────

    def _get_budget(self, key: str) -> int:
        """从 context_settings 读取 Token 预算，不存在则使用默认值"""
        return getattr(context_settings, key, self._DEFAULT_BUDGETS.get(key, 0))

    # ── 委托实例（按需创建） ──────────────────────────────────────

    @property
    def compressor(self):
        """L4: ProgressiveCompressor 实例"""
        if self._compressor is None:
            from Django_xm.apps.context_manager.services.progressive_compressor import (
                ProgressiveCompressor,
            )

            self._compressor = ProgressiveCompressor(
                model_name=self._model_name,
                store=self._store,
                user_id=self._user_id,
                thread_id=self._thread_id,
            )
        return self._compressor

    @property
    def knowledge_graph(self):
        """L5: ContextKnowledgeGraph 实例"""
        if self._knowledge_graph is None:
            from Django_xm.apps.context_manager.services.knowledge_graph import (
                ContextKnowledgeGraph,
            )

            self._knowledge_graph = ContextKnowledgeGraph(store=self._store)
        return self._knowledge_graph

    # ── 各层加载 ──────────────────────────────────────────────────

    def _load_l1_organization_policy(self) -> str:
        """L1: 组织级策略"""
        try:
            return _fetch_organization_policy()
        except Exception:
            logger.exception("加载 L1 组织策略失败")
            return ""

    def _load_l2_user_global_rules(self) -> str:
        """L2: 用户全局规则"""
        try:
            return _fetch_user_global_rules(self._user_id)
        except Exception:
            logger.exception("加载 L2 用户全局规则失败: user=%s", self._user_id)
            return ""

    def _load_l3_project_rules(self) -> str:
        """L3: 项目规则"""
        try:
            return _fetch_project_rules(self._user_id, self._project_id)
        except Exception:
            logger.exception("加载 L3 项目规则失败: user=%s, project=%s", self._user_id, self._project_id)
            return ""

    def _load_l6_auto_memory(self) -> str:
        """L6: 自动记忆"""
        try:
            return _fetch_auto_memory(self._user_id, self._project_id)
        except Exception:
            logger.exception("加载 L6 自动记忆失败: user=%s, project=%s", self._user_id, self._project_id)
            return ""

    # ── 异步版本 ──────────────────────────────────────────────────

    async def _aload_l1_organization_policy(self) -> str:
        try:
            return await sync_to_async(_fetch_organization_policy)()
        except Exception:
            logger.exception("异步加载 L1 组织策略失败")
            return ""

    async def _aload_l2_user_global_rules(self) -> str:
        try:
            return await sync_to_async(_fetch_user_global_rules)(self._user_id)
        except Exception:
            logger.exception("异步加载 L2 用户全局规则失败: user=%s", self._user_id)
            return ""

    async def _aload_l3_project_rules(self) -> str:
        try:
            return await sync_to_async(_fetch_project_rules)(self._user_id, self._project_id)
        except Exception:
            logger.exception("异步加载 L3 项目规则失败: user=%s, project=%s", self._user_id, self._project_id)
            return ""

    async def _aload_l6_auto_memory(self) -> str:
        try:
            return await sync_to_async(_fetch_auto_memory)(self._user_id, self._project_id)
        except Exception:
            logger.exception("异步加载 L6 自动记忆失败: user=%s, project=%s", self._user_id, self._project_id)
            return ""

    # ── Token 截断 ────────────────────────────────────────────────

    @staticmethod
    def _truncate_to_budget(text: str, budget: int) -> str:
        """按 Token 预算截断文本（粗估：1 token ≈ 4 字符 / 0.75 中文字）"""
        if not text or budget <= 0:
            return ""
        # 粗估 token 数：英文约 4 字符/token，中文约 1.5 字符/token
        # 统一用 3 字符/token 作为折中
        max_chars = budget * 3
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[truncated]"

    # ── 核心方法 ──────────────────────────────────────────────────

    def load_context(self, query: str = "") -> str:
        """按层级拼接上下文，各层用 XML 标签分隔

        只返回 L1+L2+L3+L6 的拼接结果。
        L4（对话历史）和 L5（向量检索）由中间件独立管理。

        Args:
            query: 当前查询（预留扩展，当前未使用）

        Returns:
            拼接后的上下文字符串
        """
        if _is_async_context():
            logger.warning("load_context 在异步上下文中被同步调用，建议使用 aload_context")
            # 异步上下文中不能直接 ORM，返回空
            return ""

        sections = []

        # L1: 组织级策略
        l1_budget = self._get_budget("memory_l1_budget")
        l1_content = self._truncate_to_budget(self._load_l1_organization_policy(), l1_budget)
        if l1_content:
            sections.append(f"<organization_policy>\n{l1_content}\n</organization_policy>")

        # L2: 用户全局规则
        l2_budget = self._get_budget("memory_l2_budget")
        l2_content = self._truncate_to_budget(self._load_l2_user_global_rules(), l2_budget)
        if l2_content:
            sections.append(f"<user_global_rules>\n{l2_content}\n</user_global_rules>")

        # L3: 项目规则
        l3_budget = self._get_budget("memory_l3_budget")
        l3_content = self._truncate_to_budget(self._load_l3_project_rules(), l3_budget)
        if l3_content:
            sections.append(f"<project_rules>\n{l3_content}\n</project_rules>")

        # L6: 自动记忆
        l6_budget = self._get_budget("memory_l6_budget")
        l6_content = self._truncate_to_budget(self._load_l6_auto_memory(), l6_budget)
        if l6_content:
            sections.append(f"<auto_memory>\n{l6_content}\n</auto_memory>")

        result = "\n\n".join(sections)
        if result:
            logger.debug(
                "分层记忆加载完成: user=%s, project=%s, 总长度=%d 字符",
                self._user_id,
                self._project_id,
                len(result),
            )
        return result

    async def aload_context(self, query: str = "") -> str:
        """异步版本的 load_context"""
        sections = []

        # 并行加载各层
        import asyncio as _asyncio

        l1_task = self._aload_l1_organization_policy()
        l2_task = self._aload_l2_user_global_rules()
        l3_task = self._aload_l3_project_rules()
        l6_task = self._aload_l6_auto_memory()

        l1_content, l2_content, l3_content, l6_content = await _asyncio.gather(
            l1_task,
            l2_task,
            l3_task,
            l6_task,
        )

        # L1
        l1_budget = self._get_budget("memory_l1_budget")
        l1_text = self._truncate_to_budget(l1_content, l1_budget)
        if l1_text:
            sections.append(f"<organization_policy>\n{l1_text}\n</organization_policy>")

        # L2
        l2_budget = self._get_budget("memory_l2_budget")
        l2_text = self._truncate_to_budget(l2_content, l2_budget)
        if l2_text:
            sections.append(f"<user_global_rules>\n{l2_text}\n</user_global_rules>")

        # L3
        l3_budget = self._get_budget("memory_l3_budget")
        l3_text = self._truncate_to_budget(l3_content, l3_budget)
        if l3_text:
            sections.append(f"<project_rules>\n{l3_text}\n</project_rules>")

        # L6
        l6_budget = self._get_budget("memory_l6_budget")
        l6_text = self._truncate_to_budget(l6_content, l6_budget)
        if l6_text:
            sections.append(f"<auto_memory>\n{l6_text}\n</auto_memory>")

        result = "\n\n".join(sections)
        if result:
            logger.debug(
                "异步分层记忆加载完成: user=%s, project=%s, 总长度=%d 字符",
                self._user_id,
                self._project_id,
                len(result),
            )
        return result

    # ── 保存自动记忆 ──────────────────────────────────────────────

    def save_auto_memory(self, content: str, source: str = "other", tags: list | None = None) -> bool:
        """保存自动记忆

        Args:
            content: 记忆内容
            source: 来源类型（build_command/debug_insight/preference/pattern/other）
            tags: 相关标签列表

        Returns:
            是否保存成功
        """
        if not content or not content.strip():
            return False

        if _is_async_context():
            logger.warning("save_auto_memory 在异步上下文中被同步调用，建议使用 asave_auto_memory")
            return False

        try:
            success = _save_auto_memory_sync(
                self._user_id,
                self._project_id,
                content.strip(),
                source,
                tags or [],
            )
            if success:
                _trim_auto_memory_sync(self._user_id, self._project_id)
            return success
        except Exception:
            logger.exception("保存自动记忆失败: user=%s", self._user_id)
            return False

    async def asave_auto_memory(self, content: str, source: str = "other", tags: list | None = None) -> bool:
        """异步保存自动记忆"""
        if not content or not content.strip():
            return False

        try:
            success = await sync_to_async(_save_auto_memory_sync)(
                self._user_id,
                self._project_id,
                content.strip(),
                source,
                tags or [],
            )
            if success:
                await sync_to_async(_trim_auto_memory_sync)(self._user_id, self._project_id)
            return success
        except Exception:
            logger.exception("异步保存自动记忆失败: user=%s", self._user_id)
            return False
