import json
import asyncio
import time
import re
from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import ChatRequestSerializer, ChatResponseSerializer
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import get_tools_for_request, WEATHER_TOOLS
from Django_xm.apps.deep_research import create_deep_research_agent, should_use_deep_research
from Django_xm.apps.core.prompts import SYSTEM_PROMPTS
from Django_xm.apps.core.models import get_chat_model

import logging

logger = logging.getLogger(__name__)


def _needs_completion(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    if len(t) < 30:
        return True
    if not any(t.endswith(p) for p in ["。", "！", "？", ".", "!", "?"]):
        return True
    return False


def _lcp_len(a: str, b: str) -> int:
    i = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        i += 1
    return i


def convert_chat_history(messages) -> list:
    """
    将 API 的消息格式转换为 LangChain 的消息格式

    Args:
        messages: API 消息列表（字典列表）

    Returns:
        LangChain 消息列表
    """
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    if not messages:
        return []

    langchain_messages = []
    for msg in messages:
        role = msg.get('role', '')
        content = msg.get('content', '')
        if role == 'user':
            langchain_messages.append(HumanMessage(content=content))
        elif role == 'assistant':
            langchain_messages.append(AIMessage(content=content))
        elif role == 'system':
            langchain_messages.append(SystemMessage(content=content))

    return langchain_messages


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
                    suggestions: list[str] = []
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            suggestions = [str(x) for x in parsed if isinstance(x, (str, int, float))]
                            suggestions = [s for s in suggestions if s.strip()][:4]
                    except Exception:
                        m = re.search(r"\[.*\]", raw, re.DOTALL)
                        if m:
                            try:
                                parsed2 = json.loads(m.group(0))
                                if isinstance(parsed2, list):
                                    suggestions = [str(x) for x in parsed2 if isinstance(x, (str, int, float))]
                                    suggestions = [s for s in suggestions if s.strip()][:4]
                            except Exception:
                                suggestions = []
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
    def get(self, request):
        modes = {}
        for mode_name, prompt in SYSTEM_PROMPTS.items():
            modes[mode_name] = prompt.split('\n')[0]
        return Response({
            'modes': modes,
            'default': 'default'
        })