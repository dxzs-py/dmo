"""
工作流 API Views
提供学习工作流的 HTTP 接口
"""

import logging
import uuid
import json
from typing import Optional, Dict, Any
from datetime import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view

from .serializers import (
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
    WorkflowStatusSerializer,
    WorkflowResponseSerializer,
)
from .models import WorkflowSession
from Django_xm.apps.workflows.study_flow import create_study_flow
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class WorkflowStartView(APIView):
    """
    启动学习工作流
    
    工作流会自动执行以下步骤：
    1. 分析用户问题，生成学习计划
    2. 检索相关文档
    3. 生成练习题
    4. 暂停，等待用户提交答案
    """
    
    def post(self, request):
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        thread_id = data.get('thread_id') or f"study_{uuid.uuid4().hex[:12]}"
        
        logger.info(f"[API] 启动工作流，thread_id={thread_id}, question={data['user_question']}")
        
        try:
            # 创建工作流会话记录
            session = WorkflowSession.objects.create(
                thread_id=thread_id,
                user_question=data['user_question'],
                status='running'
            )
            
            # 创建工作流实例
            study_flow = create_study_flow(thread_id=thread_id)
            
            # 启动工作流
            inputs = {
                "user_question": data['user_question'],
                "current_step": "initializing",
                "retry_count": 0
            }
            
            config = {
                "configurable": {"thread_id": thread_id}
            }
            
            # 执行工作流到暂停点
            result = study_flow.invoke(inputs, config=config)
            
            # 更新会话状态
            session.status = 'waiting_for_answers'
            session.current_step = result.get('current_step', 'unknown')
            session.learning_plan = result.get('learning_plan')
            session.quiz = result.get('quiz')
            session.save()
            
            # 检查是否有错误
            if result.get("error"):
                session.status = 'failed'
                session.error_message = result.get('error')
                session.save()
                
                return Response({
                    'error': f"工作流执行失败：{result['error']}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            logger.info(f"[API] 工作流启动成功，thread_id={thread_id}")
            
            return Response(WorkflowResponseSerializer({
                'thread_id': thread_id,
                'status': 'waiting_for_answers',
                'current_step': result.get('current_step', 'unknown'),
                'learning_plan': result.get('learning_plan'),
                'quiz': result.get('quiz'),
                'message': '学习计划和练习题已生成，请提交答案。'
            }).data)
            
        except Exception as e:
            logger.error(f"[API] 启动工作流失败：{str(e)}", exc_info=True)
            
            # 更新会话状态为失败
            if 'session' in locals():
                session.status = 'failed'
                session.error_message = str(e)
                session.save()
            
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class WorkflowSubmitAnswersView(APIView):
    """
    提交用户答案，继续执行工作流
    
    工作流会自动执行以下步骤：
    1. 对用户答案进行评分
    2. 生成个性化反馈
    3. 根据得分决定是否重新出题
    """
    
    def post(self, request):
        serializer = WorkflowSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        thread_id = data['thread_id']
        
        logger.info(f"[API] 提交答案，thread_id={thread_id}")
        
        try:
            # 获取工作流会话
            session = WorkflowSession.objects.get(thread_id=thread_id)
            
            # 创建工作流实例
            study_flow = create_study_flow(thread_id=thread_id)
            
            # 准备输入
            config = {
                "configurable": {"thread_id": thread_id}
            }
            
            # 获取当前状态
            current_state = study_flow.get_state(thread_id)
            
            # 更新状态，添加用户答案
            new_values = {
                "user_answers": data['answers'],
                "current_step": "submitting_answers"
            }
            study_flow.update_state(thread_id, new_values)
            
            # 继续执行工作流
            result = study_flow.invoke({"continue": True}, config=config)
            
            # 提取结果
            score = result.get('score', 0)
            score_details = result.get('score_details', {})
            feedback = result.get('feedback', '')
            should_retry = result.get('should_retry', False)
            
            # 确定状态
            if should_retry:
                workflow_status = "retry"
                message = "得分未达标，已重新生成练习题，请继续答题。"
            else:
                if score >= 60:
                    workflow_status = "completed"
                    message = "恭喜通过测验！"
                else:
                    workflow_status = "failed"
                    message = "已达到最大重试次数，建议复习后再来挑战。"
            
            # 更新会话
            session.status = workflow_status
            session.score = score
            session.score_details = score_details
            session.feedback = feedback
            session.should_retry = should_retry
            session.save()
            
            logger.info(f"[API] 答案提交成功，thread_id={thread_id}, status={workflow_status}")
            
            return Response(WorkflowResponseSerializer({
                'thread_id': thread_id,
                'status': workflow_status,
                'score': score,
                'score_details': score_details,
                'feedback': feedback,
                'should_retry': should_retry,
                'message': message
            }).data)
            
        except WorkflowSession.DoesNotExist:
            return Response({
                'error': '工作流会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)
            
        except Exception as e:
            logger.error(f"[API] 提交答案失败：{str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class WorkflowStatusView(APIView):
    """
    获取工作流的当前状态
    """
    
    def get(self, request, thread_id):
        try:
            logger.info(f"[API] 查询工作流状态，thread_id={thread_id}")
            
            # 从数据库获取会话信息
            session = WorkflowSession.objects.get(thread_id=thread_id)
            
            # 从工作流获取最新状态
            study_flow = create_study_flow(thread_id=thread_id)
            state = study_flow.get_state(thread_id)
            
            response_data = {
                'thread_id': thread_id,
                'current_step': session.current_step,
                'created_at': session.created_at.isoformat() if session.created_at else None,
                'updated_at': session.updated_at.isoformat() if session.updated_at else None,
                'status': session.status,
                'state': state or {}
            }
            
            return Response(WorkflowStatusSerializer(response_data).data)
            
        except WorkflowSession.DoesNotExist:
            return Response({
                'error': '工作流会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)
            
        except Exception as e:
            logger.error(f"[API] 查询状态失败：{str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class WorkflowHistoryView(APIView):
    """
    获取工作流的执行历史
    """
    
    def get(self, request, thread_id):
        try:
            logger.info(f"[API] 查询工作流历史，thread_id={thread_id}")
            
            # 从数据库获取会话历史
            session = WorkflowSession.objects.get(thread_id=thread_id)
            
            # 获取工作流历史
            study_flow = create_study_flow(thread_id=thread_id)
            history = []
            
            # 如果有检查点，获取历史记录
            try:
                graph = study_flow.graph
                if hasattr(graph, 'get_state_history'):
                    history_states = graph.get_state_history(
                        config={"configurable": {"thread_id": thread_id}}
                    )
                    history = [state.values for state in history_states] if history_states else []
            except Exception:
                pass
            
            response_data = {
                'thread_id': thread_id,
                'session_info': {
                    'user_question': session.user_question,
                    'status': session.status,
                    'created_at': session.created_at.isoformat() if session.created_at else None,
                    'updated_at': session.updated_at.isoformat() if session.updated_at else None,
                },
                'history': history
            }
            
            return Response(response_data)
            
        except WorkflowSession.DoesNotExist:
            return Response({
                'error': '工作流会话不存在'
            }, status=status.HTTP_404_NOT_FOUND)
            
        except Exception as e:
            logger.error(f"[API] 查询历史失败：{str(e)}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def workflow_stream(request, thread_id):
    """
    流式获取工作流执行进度（Server-Sent Events）
    
    由于 Django REST Framework 对 SSE 支持有限，这里返回一个简化的轮询接口
    前端可以定期调用此接口获取最新状态
    """
    try:
        session = WorkflowSession.objects.get(thread_id=thread_id)
        
        # 返回当前状态作为 SSE 事件
        event_data = {
            'type': 'state_update',
            'thread_id': thread_id,
            'current_step': session.current_step,
            'status': session.status,
            'updated_at': session.updated_at.isoformat() if session.updated_at else None,
        }
        
        # 如果有练习题或学习计划，也包含在响应中
        if session.quiz:
            event_data['quiz'] = session.quiz
        if session.learning_plan:
            event_data['learning_plan'] = session.learning_plan
        if session.feedback:
            event_data['feedback'] = session.feedback
        
        # 返回 SSE 格式
        from django.http import StreamingHttpResponse
        
        def event_stream():
            yield f"data: {json.dumps(event_data)}\n\n"
        
        return StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            }
        )
        
    except WorkflowSession.DoesNotExist:
        return Response({
            'error': '工作流会话不存在'
        }, status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        logger.error(f"[API] 流式查询失败：{str(e)}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
