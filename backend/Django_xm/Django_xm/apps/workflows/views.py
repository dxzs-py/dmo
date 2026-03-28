import json
import uuid
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .study_flow import create_study_flow, get_study_flow_app
from .state import StudyFlowState
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


@api_view(['POST'])
def workflow_start(request):
    """
    启动学习工作流
    
    工作流会自动执行以下步骤：
    1. 分析用户问题，生成学习计划
    2. 检索相关文档
    3. 生成练习题
    4. 暂停，等待用户提交答案
    """
    try:
        user_question = request.data.get('user_question')
        thread_id = request.data.get('thread_id')
        
        if not user_question:
            return Response({
                'error': 'user_question 参数必填'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        thread_id = thread_id or f"study_{uuid.uuid4().hex[:12]}"
        
        logger.info(f"[API] 启动工作流，thread_id={thread_id}, question={user_question[:50]}...")
        
        study_flow = create_study_flow(thread_id=thread_id)
        
        initial_state: StudyFlowState = {
            "messages": [],
            "user_question": user_question,
            "learning_plan": None,
            "retrieved_docs": None,
            "quiz": None,
            "user_answers": None,
            "score": None,
            "score_details": None,
            "feedback": None,
            "retry_count": 0,
            "should_retry": False,
            "current_step": "start",
            "thread_id": thread_id,
        }
        
        config = {
            "configurable": {"thread_id": thread_id}
        }
        
        result = study_flow.invoke(initial_state, config=config)
        
        logger.info(f"[API] 工作流启动成功，thread_id={thread_id}")
        
        return Response({
            'thread_id': thread_id,
            'status': result.get('current_step', 'unknown'),
            'learning_plan': result.get('learning_plan'),
            'quiz': result.get('quiz'),
            'message': '学习计划和练习题已生成'
        })
        
    except Exception as e:
        logger.error(f"[API] 启动工作流失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def workflow_submit(request):
    """
    提交用户答案，继续执行工作流
    """
    try:
        thread_id = request.data.get('thread_id')
        answers = request.data.get('answers')
        
        if not thread_id or not answers:
            return Response({
                'error': 'thread_id 和 answers 参数必填'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"[API] 提交答案，thread_id={thread_id}")
        
        from Django_xm.apps.workflows.study_flow import submit_answers
        result = submit_answers(thread_id, answers)
        
        logger.info(f"[API] 答案提交成功，thread_id={thread_id}")
        
        return Response({
            'thread_id': thread_id,
            'status': result.get('current_step'),
            'score': result.get('score'),
            'score_details': result.get('score_details'),
            'feedback': result.get('feedback'),
            'should_retry': result.get('should_retry'),
            'message': '答案已提交并评分'
        })
        
    except Exception as e:
        logger.error(f"[API] 提交答案失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def workflow_status(request, thread_id):
    """
    获取工作流的当前状态
    """
    try:
        logger.info(f"[API] 查询工作流状态，thread_id={thread_id}")
        
        from Django_xm.apps.workflows.study_flow import get_workflow_state
        state = get_workflow_state(thread_id)
        
        if not state:
            return Response({
                'error': '工作流会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'thread_id': thread_id,
            'current_step': state.get('current_step'),
            'status': state.get('current_step'),
            'state': state
        })
        
    except Exception as e:
        logger.error(f"[API] 查询状态失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def workflow_history(request, thread_id):
    """
    获取工作流的执行历史
    """
    try:
        logger.info(f"[API] 查询工作流历史，thread_id={thread_id}")
        
        from Django_xm.apps.workflows.study_flow import get_workflow_history
        history = get_workflow_history(thread_id)
        
        return Response({
            'thread_id': thread_id,
            'history': history
        })
        
    except Exception as e:
            logger.error(f"[API] 查询历史失败：{str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def workflow_stream(request, thread_id):
    """
    流式获取工作流执行进度（Server-Sent Events）
    使用 LangGraph 的 stream 方法获取真实的事件流
    """
    try:
        logger.info(f"[API] 流式获取工作流，thread_id={thread_id}")
        
        def event_stream():
            """生成 SSE 事件流"""
            try:
                from .study_flow import get_workflow_state, _get_study_flow
                
                # 获取当前状态
                state = get_workflow_state(thread_id)
                
                if not state:
                    yield f"data: {json.dumps({'type': 'error', 'message': '工作流不存在'})}\n\n"
                    return
                
                # 发送初始状态
                yield f"data: {json.dumps({'type': 'start', 'state': state.get('current_step')})}\n\n"
                
                # 获取工作流实例
                study_flow = _get_study_flow(thread_id)
                config = {"configurable": {"thread_id": thread_id}}
                
                # 使用 stream 方法获取真实的事件流
                # 注意：这里使用同步的 stream 方法而不是异步的 astream_events
                try:
                    for event in study_flow.graph.stream(None, config, stream_mode="values"):
                        # 发送状态更新事件
                        if event:
                            current_step = event.get('current_step', 'unknown')
                            yield f"data: {json.dumps({'type': 'state_update', 'step': current_step, 'data': event})}\n\n"
                    
                    # 发送完成事件
                    yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                    
                except Exception as stream_error:
                    # 如果流式执行失败，发送错误事件
                    logger.warning(f"[API] 流式执行失败：{stream_error}")
                    yield f"data: {json.dumps({'type': 'stream_error', 'message': str(stream_error)})}\n\n"
                    # 发送完成事件（即使出错也标记完成）
                    yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                
            except Exception as e:
                logger.error(f"[API] 流式输出失败：{str(e)}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        
        return StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )
        
    except Exception as e:
        logger.error(f"[API] 流式输出失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
def workflow_delete(request, thread_id):
    """
    删除工作流及其检查点数据
    """
    try:
        logger.info(f"[API] 删除工作流，thread_id={thread_id}")
        
        from .study_flow import _study_flow_cache
        
        # 从缓存中删除
        if thread_id in _study_flow_cache:
            del _study_flow_cache[thread_id]
        
        # 注意：LangGraph 的 MemorySaver 没有直接的删除接口
        # 如果使用 SQLite 检查点，可以考虑直接操作数据库
        
        return Response({
            'thread_id': thread_id,
            'status': 'deleted',
            'message': '工作流已删除（注意：检查点数据可能仍然存在）'
        })
        
    except Exception as e:
        logger.error(f"[API] 删除工作流失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
