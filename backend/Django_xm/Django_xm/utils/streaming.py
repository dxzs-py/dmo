"""
流式响应工具模块
提供统一的流式响应处理功能
"""
import json
import logging
from typing import AsyncGenerator, Dict, Any

logger = logging.getLogger(__name__)


async def generate_streaming_response(
    response_generator: AsyncGenerator[str, None],
    source: str = "AI"
) -> AsyncGenerator[str, None]:
    """
    生成统一格式的流式响应
    
    Args:
        response_generator: 响应生成器
        source: 响应来源标识
    
    Yields:
        SSE格式的响应数据
    """
    buffer = ""
    try:
        async for chunk in response_generator:
            buffer += chunk
            
            yield f"data: {json.dumps({'type': 'content', 'content': chunk, 'source': source})}\n\n"
        
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        
    except Exception as e:
        logger.error(f"流式响应生成错误: {str(e)}", exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


def create_streaming_chunk(content: str, chunk_type: str = "content", **kwargs) -> str:
    """
    创建单个流式响应块
    
    Args:
        content: 响应内容
        chunk_type: 块类型 (content, error, done等)
        **kwargs: 额外的响应数据
    
    Returns:
        SSE格式的响应数据
    """
    data = {'type': chunk_type, 'content': content, **kwargs}
    return f"data: {json.dumps(data)}\n\n"
