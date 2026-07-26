"""
JSON 工具模块
提供项目通用的 JSON 编码器
"""
import json


class WorkflowJSONEncoder(json.JSONEncoder):
    """工作流 JSON 编码器，支持 LangChain 消息等复杂对象序列化"""

    def default(self, obj):
        from langchain_core.messages import BaseMessage
        if isinstance(obj, BaseMessage):
            return {"type": obj.type, "content": obj.content}
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        if hasattr(obj, 'dict'):
            return obj.dict()
        if hasattr(obj, '__dict__'):
            return str(obj)
        return super().default(obj)
