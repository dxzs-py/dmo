import json
import asyncio
import time
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import ChatRequestSerializer, ChatResponseSerializer
from Django_xm.libs.langchain_core.agents import create_base_agent
from Django_xm.libs.langchain_core.tools import get_tools_for_request
from Django_xm.libs.langchain_core.deep_research import create_deep_research_agent, should_use_deep_research
import logging

logger = logging.getLogger(__name__)

class ChatView(APIView):
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        logger.info(f"收到聊天请求: {data['message'][:50]}...")
        
        try:
            tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
            agent = create_base_agent(tools=tools, prompt_mode=data['mode'])
            
            chat_history = data.get('chat_history', [])
            
            response = agent.invoke(
                input_text=data['message'],
                chat_history=chat_history,
                config={"recursion_limit": 50}
            )
            
            tool_names = [tool.name for tool in tools]
            
            logger.info(f"聊天请求处理完成，响应长度: {len(response)} 字符")
            
            return Response(ChatResponseSerializer({
                'message': response,
                'mode': data['mode'],
                'tools_used': tool_names,
                'success': True
            }).data)
            
        except Exception as e:
            error_msg = f"处理聊天请求时出错: {str(e)}"
            logger.error(error_msg)
            
            return Response(ChatResponseSerializer({
                'message': '抱歉，处理您的请求时出现错误。',
                'mode': data['mode'],
                'tools_used': [],
                'success': False,
                'error': str(e)
            }).data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChatStreamView(APIView):
    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        logger.info(f"收到流式聊天请求: {data['message'][:50]}...")
        
        async def generate():
            try:
                yield f"data: {json.dumps({'type': 'start', 'message': '开始生成...'}, ensure_ascii=False)}\n\n"
                
                if should_use_deep_research(data['message']):
                    logger.info("触发深度研究流程")
                    
                    yield f"data: {json.dumps({'type': 'reasoning', 'data': {'content': '问题较为复杂，正在调度深度研究工作流...'}}, ensure_ascii=False)}\n\n"
                    
                    agent = create_deep_research_agent(
                        thread_id=f"deep_{int(time.time() * 1000)}",
                        enable_web_search=True,
                        enable_doc_analysis=False
                    )
                    deep_result = agent.research(data['message'])
                    final_report = deep_result.get('final_report') or deep_result.get('error', '深度研究已完成')
                    
                    yield f"data: {json.dumps({'type': 'chunk', 'content': final_report}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"
                    return
                
                tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
                agent = create_base_agent(tools=tools, prompt_mode=data['mode'])
                
                chat_history = data.get('chat_history', [])
                messages = []
                if chat_history:
                    messages.extend(chat_history)
                
                from langchain_core.messages import HumanMessage
                messages.append(HumanMessage(content=data['message']))
                
                current_content = ""
                
                async for chunk in agent.graph.astream(
                    {"messages": messages},
                    config={"recursion_limit": 50},
                    stream_mode="messages"
                ):
                    if hasattr(chunk, 'content') and chunk.content:
                        new_content = chunk.content[len(current_content):] if len(chunk.content) > len(current_content) else chunk.content
                        if new_content:
                            current_content = chunk.content
                            yield f"data: {json.dumps({'type': 'chunk', 'content': new_content}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.01)
                
                yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"
                
            except Exception as e:
                logger.error(f"流式处理出错: {str(e)}")
                yield f"data: {json.dumps({'type': 'error', 'message': '抱歉，处理您的请求时出现错误', 'error': str(e)}, ensure_ascii=False)}\n\n"
        
        def sync_generate():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                gen = generate()
                while True:
                    try:
                        yield loop.run_until_complete(gen.__anext__())
                    except StopAsyncIteration:
                        break
            finally:
                loop.close()
        
        return StreamingHttpResponse(
            sync_generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )

class ChatModesView(APIView):
    def get(self, request):
        from Django_xm.libs.langchain_core.prompts import SYSTEM_PROMPTS
        modes = {}
        for mode_name, prompt in SYSTEM_PROMPTS.items():
            modes[mode_name] = prompt.split('\n')[0]
        return Response({
            'modes': modes,
            'default': 'default'
        })
