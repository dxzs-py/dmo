"""
本地自定义 MCP 工具模块

提供项目级别的自定义工具，与远程 MCP Server 工具统一注册到 Agent 工具列表中。
每个工具遵循 LangChain BaseTool 规范，可被 LangGraph 和 LangChain Agent 直接调用。

工具列表:
    - ProjectInfoTool: 项目信息查询
    - SystemStatusTool: 系统状态检查
    - DataQueryTool: 数据查询工具（示例）
"""

from typing import List, Optional, Type, ClassVar, Dict
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from Django_xm.apps.ai_engine.config import get_logger

logger = get_logger(__name__)


class ProjectInfoInput(BaseModel):
    query: str = Field(description="查询内容，如 '版本'、'技术栈'、'架构'")


class ProjectInfoTool(BaseTool):
    name: str = "project_info"
    description: str = (
        "查询当前项目信息，包括版本、技术栈、架构设计、模块划分等。"
        "输入查询关键词，返回对应的项目信息。"
    )
    args_schema: Type[BaseModel] = ProjectInfoInput

    def _run(self, query: str) -> str:
        query_lower = query.lower()

        project_info = {
            "version": "1.0.0",
            "name": "LangChain XM - AI智能体平台",
            "tech_stack": {
                "backend": "Django 5.2+ / DRF / Celery / MySQL / Redis",
                "frontend": "Vue 3.5 / Pinia / Element Plus / Tailwind CSS",
                "ai": "LangChain 1.2+ / LangGraph 1.1+ / MCP / FAISS",
            },
            "architecture": "Django MTV + Vue3 SPA，前后端分离，RESTful API",
            "modules": [
                "ai_engine - AI引擎核心（Agent/Chain/LLM/RAG）",
                "chat - 对话管理",
                "tools - 工具集成（内置工具/MCP/自定义）",
                "knowledge - 知识库管理",
                "users - 用户认证与权限",
            ],
            "mcp_servers": [
                "sequential-thinking - 结构化渐进式思维",
                "context7 - 实时库文档查询",
            ],
        }

        if "版本" in query_lower or "version" in query_lower:
            return f"项目版本: {project_info['version']}"
        elif "技术栈" in query_lower or "tech" in query_lower:
            return f"技术栈:\n{self._format_dict(project_info['tech_stack'])}"
        elif "架构" in query_lower or "architecture" in query_lower:
            return f"架构: {project_info['architecture']}"
        elif "模块" in query_lower or "module" in query_lower:
            modules = "\n".join(f"  - {m}" for m in project_info["modules"])
            return f"项目模块:\n{modules}"
        elif "mcp" in query_lower:
            servers = "\n".join(f"  - {s}" for s in project_info["mcp_servers"])
            return f"MCP 服务器:\n{servers}"
        else:
            import json
            return json.dumps(project_info, ensure_ascii=False, indent=2)

    async def _arun(self, query: str) -> str:
        return self._run(query)

    @staticmethod
    def _format_dict(d: dict) -> str:
        return "\n".join(f"  {k}: {v}" for k, v in d.items())


class SystemStatusInput(BaseModel):
    check_type: str = Field(
        default="all",
        description="检查类型: 'all'-全部, 'database'-数据库, 'cache'-缓存, 'mcp'-MCP连接",
    )


class SystemStatusTool(BaseTool):
    name: str = "system_status"
    description: str = (
        "检查系统运行状态，包括数据库连接、缓存服务、MCP服务器连接等。"
        "输入检查类型，返回对应的状态信息。"
    )
    args_schema: Type[BaseModel] = SystemStatusInput

    def _run(self, check_type: str = "all") -> str:
        results = []

        if check_type in ("all", "database"):
            results.append(self._check_database())

        if check_type in ("all", "cache"):
            results.append(self._check_cache())

        if check_type in ("all", "mcp"):
            results.append(self._check_mcp())

        return "\n\n".join(results)

    async def _arun(self, check_type: str = "all") -> str:
        from asgiref.sync import sync_to_async
        return await sync_to_async(self._run, thread_sensitive=True)(check_type)

    @staticmethod
    def _check_database() -> str:
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return "✅ 数据库: 连接正常"
        except Exception as e:
            return f"❌ 数据库: 连接失败 - {e}"

    @staticmethod
    def _check_cache() -> str:
        try:
            from django.core.cache import cache
            cache.set("_health_check", "ok", 10)
            value = cache.get("_health_check")
            if value == "ok":
                return "✅ 缓存: 连接正常"
            return "⚠️ 缓存: 读写异常"
        except Exception as e:
            return f"❌ 缓存: 连接失败 - {e}"

    @staticmethod
    def _check_mcp() -> str:
        try:
            from . import get_server_info_list, get_pooled_client_count
            servers = get_server_info_list()
            pool_count = get_pooled_client_count()

            if not servers:
                return "⚠️ MCP: 未配置服务器"

            lines = [f"✅ MCP: {len(servers)} 个服务器已配置, {pool_count} 个客户端活跃"]
            for srv in servers:
                status = "启用" if srv.get("enabled", True) else "禁用"
                lines.append(f"  - {srv['name']} ({srv['transport']}) [{status}]")

            return "\n".join(lines)
        except Exception as e:
            return f"❌ MCP: 检查失败 - {e}"


class DataQueryInput(BaseModel):
    model_name: str = Field(description="Django 模型名称，如 'User', 'Conversation', 'KnowledgeBase'")
    action: str = Field(
        default="count",
        description="操作类型: 'count'-计数, 'list'-列表(前10条), 'schema'-模型结构",
    )
    filters: Optional[str] = Field(
        default=None,
        description="过滤条件，JSON格式，如 '{\"is_active\": true}'",
    )


class DataQueryTool(BaseTool):
    name: str = "data_query"
    description: str = (
        "查询项目数据，支持统计计数、列表查看和模型结构查看。"
        "可查询的模型: User, Conversation, Message, KnowledgeBase, Document。"
        "输入模型名称和操作类型，返回查询结果。"
    )
    args_schema: Type[BaseModel] = DataQueryInput

    MODEL_MAP: ClassVar[Dict[str, str]] = {
        "User": "django.contrib.auth.models.User",
        "Conversation": "Django_xm.apps.chat.models.Conversation",
        "Message": "Django_xm.apps.chat.models.Message",
        "KnowledgeBase": "Django_xm.apps.knowledge.models.KnowledgeBase",
        "Document": "Django_xm.apps.knowledge.models.Document",
    }

    def _run(self, model_name: str, action: str = "count", filters: Optional[str] = None) -> str:
        model_path = self.MODEL_MAP.get(model_name)
        if not model_path:
            available = ", ".join(self.MODEL_MAP.keys())
            return f"未知模型: {model_name}。可用模型: {available}"

        try:
            model = self._import_model(model_path)
        except Exception as e:
            return f"模型导入失败: {e}"

        try:
            queryset = model.objects.all()

            if filters:
                import json
                filter_dict = json.loads(filters)
                queryset = queryset.filter(**filter_dict)

            if action == "count":
                count = queryset.count()
                return f"{model_name} 记录数: {count}"

            elif action == "list":
                items = list(queryset[:10])
                if not items:
                    return f"{model_name} 无记录"

                lines = [f"{model_name} 列表 (前10条):"]
                for item in items:
                    lines.append(f"  - {self._model_to_str(item)}")
                return "\n".join(lines)

            elif action == "schema":
                fields = model._meta.get_fields()
                lines = [f"{model_name} 模型结构:"]
                for field in fields:
                    if hasattr(field, 'name'):
                        field_type = type(field).__name__
                        lines.append(f"  - {field.name}: {field_type}")
                return "\n".join(lines)

            else:
                return f"未知操作: {action}。可用操作: count, list, schema"

        except Exception as e:
            return f"查询失败: {e}"

    async def _arun(self, model_name: str, action: str = "count", filters: Optional[str] = None) -> str:
        from asgiref.sync import sync_to_async
        return await sync_to_async(self._run, thread_sensitive=True)(model_name, action, filters)

    @staticmethod
    def _import_model(model_path: str):
        module_path, class_name = model_path.rsplit(".", 1)
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    @staticmethod
    def _model_to_str(instance) -> str:
        if hasattr(instance, '__str__'):
            return str(instance)
        return f"ID={instance.pk}"


def get_local_mcp_tools() -> List[BaseTool]:
    tools: List[BaseTool] = [
        ProjectInfoTool(),
        SystemStatusTool(),
        DataQueryTool(),
    ]
    logger.info(f"本地 MCP 工具已注册: {[t.name for t in tools]}")
    return tools


__all__ = [
    "ProjectInfoTool",
    "SystemStatusTool",
    "DataQueryTool",
    "get_local_mcp_tools",
]
