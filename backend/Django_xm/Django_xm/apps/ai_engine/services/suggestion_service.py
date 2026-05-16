"""
动态建议生成服务

根据当前查询和上下文生成建议的后续问题
"""

import logging
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

logger = logging.getLogger(__name__)

SUGGESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是一个帮助用户生成后续问题的助手。
根据用户的当前问题和提供的上下文，生成 3 个可能的后续问题建议。

要求：
- 每个问题应该与当前问题相关，但提供不同的角度或更深入的信息
- 问题应该简洁明了
- 每个问题一行，不要编号
- 使用与用户问题相同的语言
- 不要添加任何额外的解释或格式"""),
    ("human", """当前问题：{query}

相关上下文：
{context}

请生成 3 个后续问题建议：""")
])


def generate_suggestions(
    query: str,
    context: str = "",
    model_name: Optional[str] = None,
    count: int = 3
) -> List[str]:
    try:
        llm = get_chat_model(
            model_name=model_name,
            temperature=0.7,
            streaming=False,
        )

        chain = SUGGESTION_PROMPT | llm | StrOutputParser()

        suggestions_str = chain.invoke({
            "query": query,
            "context": context[:1000] if context else "无额外上下文"
        })

        suggestions = [
            s.strip()
            for s in suggestions_str.strip().split("\n")
            if s.strip()
        ]

        logger.info(f"生成 {len(suggestions)} 个建议问题")
        return suggestions[:count]

    except Exception as e:
        logger.warning(f"生成建议失败: {e}，使用默认建议")
        return [
            f"你能详细解释一下关于 '{query}' 的内容吗？",
            f"有没有与 '{query}' 相关的实际案例？",
            f"关于这个主题，还有哪些重要信息需要了解？"
        ]
