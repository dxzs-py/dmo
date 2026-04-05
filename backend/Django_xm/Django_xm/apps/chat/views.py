import json
import asyncio
import time
import uuid
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from asgiref.sync import sync_to_async
from langchain_core.messages import HumanMessage, AIMessage

from Django_xm.apps.core.throttling import ChatStreamRateThrottle

from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChatSessionSerializer,
    ChatSessionCreateSerializer,
    ChatSessionUpdateSerializer,
    ChatMessageSerializer
)
from .models import ChatSession, ChatMessage
from .services import SecureSessionCacheService
from .utils import _needs_completion, _lcp_len, convert_chat_history, extract_suggestions
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import get_tools_for_request, WEATHER_TOOLS
from Django_xm.apps.deep_research import create_deep_research_agent, should_use_deep_research
from Django_xm.apps.core.prompts import SYSTEM_PROMPTS
from Django_xm.apps.core.models import get_chat_model

import logging

logger = logging.getLogger(__name__)


class ChatView(APIView):
    permission_classes = [IsAuthenticated]
    
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
            langchain_chat_history = convert_chat_history(chat_history)

            response = agent.invoke(
                input_text=data['message'],
                chat_history=langchain_chat_history,
                config={"recursion_limit": 50}
            )

            if _needs_completion(response):
                model = get_chat_model()
                prompt = (
                    f"用户问题：{data['message']}\n\n"
                    f"当前回复（不完整）：{response}\n\n"
                    "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
                )
                completion = model.invoke([{"role": "user", "content": prompt}])
                if getattr(completion, "content", None):
                    response = completion.content

            tool_names = [tool.name for tool in tools]

            logger.info(f"聊天请求处理完成，响应长度: {len(response)} 字符")

            return Response({
                'code': 0,
                'message': '操作成功',
                'data': ChatResponseSerializer({
                    'message': response,
                    'mode': data['mode'],
                    'tools_used': tool_names,
                    'success': True
                }).data
            })

        except Exception as e:
            error_msg = f"处理聊天请求时出错: {str(e)}"
            logger.error(error_msg)

            return Response({
                'code': 500,
                'message': '抱歉，处理您的请求时出现错误',
                'data': ChatResponseSerializer({
                    'message': '抱歉，处理您的请求时出现错误。',
                    'mode': data['mode'],
                    'tools_used': [],
                    'success': False,
                    'error': str(e)
                }).data
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChatStreamView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ChatStreamRateThrottle]

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        logger.info(f"收到流式聊天请求: {data['message'][:50]}...")

        async def run_deep_research_task(query: str):
            thread_id = f"deep_{int(time.time() * 1000)}"

            def _task():
                agent = create_deep_research_agent(
                    thread_id=thread_id,
                    enable_web_search=True,
                    enable_doc_analysis=False,
                )
                return agent.research(query)

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _task)

        async def generate():
            try:
                from Django_xm.apps.core.usage_tracker import create_usage_tracker
                from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
                
                yield f"data: {json.dumps({'type': 'start', 'message': '开始生成...'}, ensure_ascii=False)}\n\n"

                usage_tracker = create_usage_tracker()

                if should_use_deep_research(data['message']):
                    logger.info("触发深度研究流程")

                    reasoning_event = {
                        "type": "reasoning",
                        "data": {
                            "content": "问题较为复杂，正在调度深度研究工作流并执行网络搜索...",
                            "duration": 0,
                        },
                    }
                    yield f"data: {json.dumps(reasoning_event, ensure_ascii=False)}\n\n"

                    deep_result = await run_deep_research_task(data['message'])
                    final_report = deep_result.get("final_report") or deep_result.get("error")
                    if not final_report:
                        final_report = (
                            "深度研究已完成，但未生成可用报告。"
                            " 请稍后重试或调整问题表述。"
                        )

                    chunk_data = {
                        "type": "chunk",
                        "content": final_report,
                    }
                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"

                    context_info = usage_tracker.get_usage_info()
                    yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

                    yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"

                    usage_tracker.log_summary()
                    logger.info("深度研究流程完成")
                    return

                tools = get_tools_for_request(data['use_tools'], data['use_advanced_tools'])
                tool_names = [tool.name for tool in tools]
                weather_tool_names = {tool.name for tool in WEATHER_TOOLS}

                agent = create_base_agent(tools=tools, prompt_mode=data['mode'])

                chat_history = data.get('chat_history', [])
                langchain_chat_history = convert_chat_history(chat_history)
                messages = []
                if langchain_chat_history:
                    messages.extend(langchain_chat_history)

                messages.append(HumanMessage(content=data['message']))
                graph_input = {"messages": messages}

                tool_calls_map = {}
                current_message_content = ""
                last_ai_message = None
                all_messages = []
                tool_call_count = {}
                prefer_tool_result = False

                config = {"recursion_limit": 50}

                async for chunk in agent.graph.astream(graph_input, config=config, stream_mode="messages"):
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        message, metadata = chunk
                    else:
                        message = chunk
                        metadata = {}

                    if metadata:
                        usage_tracker.update_from_metadata(metadata)

                    all_messages.append(message)

                    if isinstance(message, AIMessage):
                        last_ai_message = message
                        tool_calls = getattr(message, "tool_calls", [])
                        if tool_calls:
                            for tool_call in tool_calls:
                                tool_id = tool_call.get("id", "")
                                tool_name = tool_call.get("name", "")

                                if tool_name not in tool_call_count:
                                    tool_call_count[tool_name] = 0
                                tool_call_count[tool_name] += 1

                                if tool_call_count[tool_name] > 3:
                                    logger.warning(f"工具 {tool_name} 被调用了 {tool_call_count[tool_name]} 次，可能存在循环调用")

                                tool_info = {
                                    "id": tool_id,
                                    "name": tool_name,
                                    "type": f"tool-call-{tool_name}",
                                    "state": "input-available",
                                    "parameters": tool_call.get("args", {}),
                                    "result": None,
                                    "error": None,
                                }
                                tool_calls_map[tool_id] = tool_info

                                yield f"data: {json.dumps({'type': 'tool', 'data': tool_info}, ensure_ascii=False)}\n\n"

                        if message.content and not tool_calls and not prefer_tool_result:
                            lcp = _lcp_len(current_message_content, message.content)
                            if lcp < len(message.content):
                                new_content = message.content[lcp:]
                                current_message_content = message.content
                                chunk_data = {
                                    "type": "chunk",
                                    "content": new_content,
                                }
                                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"

                    elif isinstance(message, ToolMessage):
                        tool_call_id = getattr(message, "tool_call_id", "")
                        is_error = getattr(message, "status", None) == "error"

                        if tool_call_id in tool_calls_map:
                            tool_info = tool_calls_map[tool_call_id]
                            tool_info["state"] = "output-error" if is_error else "output-available"
                            tool_info["result"] = None if is_error else message.content
                            tool_info["error"] = message.content if is_error else None

                            yield f"data: {json.dumps({'type': 'tool_result', 'data': tool_info}, ensure_ascii=False)}\n\n"

                            if (not is_error
                                    and tool_info.get("name") in weather_tool_names
                                    and tool_info.get("result")
                                    and not tool_info.get("delivered")):
                                weather_result = tool_info["result"]
                                chunk_data = {
                                    "type": "chunk",
                                    "content": weather_result,
                                }
                                yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                                current_message_content += weather_result

                                ai_message = AIMessage(content=weather_result)
                                all_messages.append(ai_message)
                                last_ai_message = ai_message

                                tool_info["delivered"] = True
                                prefer_tool_result = True

                    await asyncio.sleep(0.01)

                final_ai_message = None
                for msg in reversed(all_messages):
                    if isinstance(msg, AIMessage) and msg.content and msg.content.strip():
                        final_ai_message = msg
                        break

                if final_ai_message and final_ai_message.content:
                    final_content = final_ai_message.content
                    if len(final_content) > len(current_message_content):
                        remaining_content = final_content[len(current_message_content):]
                        if remaining_content:
                            chunk_data = {
                                "type": "chunk",
                                "content": remaining_content,
                            }
                            yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                            current_message_content = final_content

                if (not final_ai_message or not final_ai_message.content or len(final_ai_message.content.strip()) < 10) and tool_calls_map:
                    weather_tools = ["get_daily_weather", "get_weather_forecast", "get_weather"]
                    for tool_name in weather_tools:
                        for tool_info in tool_calls_map.values():
                            if (tool_info.get("name") == tool_name and
                                tool_info.get("state") == "output-available" and
                                tool_info.get("result")):
                                result_content = tool_info.get("result", "")
                                if result_content and result_content not in current_message_content:
                                    chunk_data = {
                                        "type": "chunk",
                                        "content": result_content,
                                    }
                                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                                    logger.info(f"使用工具 {tool_name} 的结果作为最终回复")
                                    break
                        else:
                            continue
                        break
                    else:
                        for tool_info in tool_calls_map.values():
                            if (tool_info.get("state") == "output-available" and
                                tool_info.get("result") and
                                tool_info.get("result") not in current_message_content):
                                result_content = tool_info.get("result", "")
                                if result_content:
                                    chunk_data = {
                                        "type": "chunk",
                                        "content": result_content,
                                    }
                                    yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                                logger.info(f"使用工具 {tool_info.get('name')} 的结果作为最终回复")
                                break

                if not prefer_tool_result and _needs_completion(current_message_content):
                    model = get_chat_model()
                    prompt = (
                        f"用户问题：{data['message']}\n\n"
                        f"当前回复（不完整）：{current_message_content}\n\n"
                        "请继续并完整回答上述问题，补充必要的解释或例子，最后给出一句简明结论。"
                    )
                    try:
                        completion = await model.ainvoke([{"role": "user", "content": prompt}])
                        extra = getattr(completion, "content", "")
                        if extra:
                            chunk_data = {
                                "type": "chunk",
                                "content": extra,
                            }
                            yield f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"
                            current_message_content += extra
                    except Exception:
                        pass

                try:
                    model = get_chat_model()
                    suggestions_prompt = (
                        "你是一个辅助对话的助手。请根据以下用户问题和最终回复，生成4条简洁、相关、可点击的后续问题建议。\n"
                        "用JSON数组返回，每个元素是不超过30字的中文字符串，不要包含编号或多余文本。\n\n"
                        f"用户问题：{data['message']}\n\n"
                        f"最终回复：{current_message_content}"
                    )
                    completion = await model.ainvoke([{"role": "user", "content": suggestions_prompt}])
                    raw = getattr(completion, "content", "")
                    suggestions = extract_suggestions(raw)
                    if suggestions:
                        yield f"data: {json.dumps({'type': 'suggestions', 'data': suggestions}, ensure_ascii=False)}\n\n"
                except Exception:
                    pass

                context_info = usage_tracker.get_usage_info()
                yield f"data: {json.dumps({'type': 'context', 'data': context_info}, ensure_ascii=False)}\n\n"

                yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'}, ensure_ascii=False)}\n\n"

                usage_tracker.log_summary()
                logger.info("流式聊天请求处理完成")

            except Exception as e:
                error_msg = f"流式处理出错: {str(e)}"
                logger.error(error_msg)
                logger.exception(e)

                error_data = {
                    "type": "error",
                    "message": "抱歉，处理您的请求时出现错误",
                    "error": str(e),
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        def sync_generate():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client_connected = True

            try:
                gen = generate()
                while client_connected:
                    try:
                        chunk = loop.run_until_complete(gen.__anext__())
                        yield chunk
                    except StopAsyncIteration:
                        break
                    except (BrokenPipeError, ConnectionResetError, IOError) as e:
                        logger.info(f"客户端断开连接，停止生成: {e}")
                        client_connected = False
                        break
                    except Exception as e:
                        logger.error(f"生成过程出错: {e}")
                        raise
            finally:
                loop.close()

        return StreamingHttpResponse(
            sync_generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )


class ChatModesView(APIView):
    permission_classes = [IsAuthenticated]

    FRONTEND_SUPPORTED_MODES = ['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded']

    def get(self, request):
        modes = {}
        for mode_name, prompt in SYSTEM_PROMPTS.items():
            if mode_name in self.FRONTEND_SUPPORTED_MODES:
                modes[mode_name] = prompt.split('\n')[0]
        return Response({
            'code': 0,
            'message': '操作成功',
            'data': {
                'modes': modes,
                'default': 'basic-agent'
            }
        })


class ChatSessionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = request.user.id

        cached_sessions = SecureSessionCacheService.get_user_sessions_list(user_id)

        if cached_sessions:
            try:
                sessions_data = []
                for session_id in cached_sessions:
                    cached_session = SecureSessionCacheService.get_cached_session(user_id, session_id)
                    if cached_session:
                        sessions_data.append(cached_session)

                if sessions_data:
                    logger.info(f"Returning {len(sessions_data)} cached sessions for user {user_id}")
                    return Response({
                        'code': 0,
                        'message': '操作成功',
                        'data': sessions_data,
                        'source': 'cache'
                    })
            except Exception as e:
                logger.warning(f"Cache read failed, falling back to DB: {str(e)}")

        sessions = ChatSession.objects.filter(
            user=request.user
        ).select_related(
            'user'
        ).prefetch_related(
            'messages'
        ).order_by('-updated_at')
        serializer = ChatSessionSerializer(sessions, many=True)
        response_data = serializer.data

        for session_data in response_data:
            SecureSessionCacheService.cache_session(user_id, session_data)

        return Response({
            'code': 0,
            'message': '操作成功',
            'data': response_data,
            'source': 'database'
        })


class ChatSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get_object(self, session_id, user):
        try:
            return ChatSession.objects.select_related(
                'user'
            ).prefetch_related(
                'messages'
            ).get(session_id=session_id, user=user)
        except ChatSession.DoesNotExist:
            return None
    
    def get(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return Response({
                'code': 404,
                'message': '会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        serializer = ChatSessionSerializer(session)
        return Response({
            'code': 0,
            'message': '操作成功',
            'data': serializer.data
        })

    def put(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return Response({
                'code': 404,
                'message': '会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        serializer = ChatSessionUpdateSerializer(session, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': ChatSessionSerializer(session).data
            })
        return Response({
            'code': 400,
            'message': '参数错误',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, session_id):
        session = self.get_object(session_id, request.user)
        if not session:
            return Response({
                'code': 404,
                'message': '会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        user_id = request.user.id
        SecureSessionCacheService.invalidate_user_session(user_id, session_id)
        session.delete()
        return Response({
            'code': 0,
            'message': '操作成功'
        })


class ChatSessionCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChatSessionCreateSerializer(data=request.data)
        if serializer.is_valid():
            session_id = str(uuid.uuid4())
            session = ChatSession.objects.create(
                session_id=session_id,
                user=request.user,
                title=serializer.validated_data.get('title', '新对话'),
                mode=serializer.validated_data.get('mode', 'basic-agent')
            )
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': ChatSessionSerializer(session).data
            })
        return Response({
            'code': 400,
            'message': '参数错误',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class ChatMessageCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_session(self, session_id, user):
        try:
            return ChatSession.objects.select_related('user').get(session_id=session_id, user=user)
        except ChatSession.DoesNotExist:
            return None

    def post(self, request, session_id):
        session = self.get_session(session_id, request.user)
        if not session:
            return Response({
                'code': 404,
                'message': '会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        data = request.data.copy()
        data['session'] = session.id

        serializer = ChatMessageSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            session.save()
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': serializer.data
            })
        return Response({
            'code': 400,
            'message': '参数错误',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class ChatMessageBatchCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_session(self, session_id, user):
        try:
            return ChatSession.objects.select_related('user').get(session_id=session_id, user=user)
        except ChatSession.DoesNotExist:
            return None

    def post(self, request, session_id):
        session = self.get_session(session_id, request.user)
        if not session:
            return Response({
                'code': 404,
                'message': '会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        messages_data = request.data.get('messages', [])
        created_messages = []

        for msg_data in messages_data:
            msg_data['session'] = session.id
            serializer = ChatMessageSerializer(data=msg_data)
            if serializer.is_valid():
                created_messages.append(serializer.save())
            else:
                return Response({
                    'code': 400,
                    'message': '参数错误',
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)

        session.save()
        return Response({
            'code': 0,
            'message': '操作成功',
            'data': ChatMessageSerializer(created_messages, many=True).data
        })


class ChatMessageUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_message(self, message_id, user):
        try:
            return ChatMessage.objects.get(id=message_id, session__user=user)
        except ChatMessage.DoesNotExist:
            return None

    def put(self, request, message_id):
        message = self.get_message(message_id, request.user)
        if not message:
            return Response({
                'code': 404,
                'message': '消息不存在'
            }, status=status.HTTP_404_NOT_FOUND)

        serializer = ChatMessageSerializer(message, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            message.session.save()
            return Response({
                'code': 0,
                'message': '操作成功',
                'data': serializer.data
            })
        return Response({
            'code': 400,
            'message': '参数错误',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
