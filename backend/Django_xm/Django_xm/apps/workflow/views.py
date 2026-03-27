import logging
import time
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import WorkflowStartSerializer, WorkflowStatusSerializer
from .models import WorkflowExecution
from Django_xm.libs.langchain_workflows.study_flow_graph import create_study_flow
import logging

logger = logging.getLogger(__name__)

class WorkflowStartView(APIView):
    def post(self, request):
        serializer = WorkflowStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        thread_id = data.get('thread_id') or f"wf_{int(time.time() * 1000)}"
        
        logger.info(f"启动工作流: thread_id={thread_id}, query={data['query'][:50]}...")
        
        try:
            execution = WorkflowExecution.objects.create(
                thread_id=thread_id,
                workflow_type=data.get('workflow_type', 'study_flow'),
                query=data['query'],
                status='running'
            )
            
            workflow = create_study_flow(thread_id=thread_id)
            result = workflow.invoke({"query": data['query']})
            
            execution.status = 'completed'
            execution.result = result
            execution.save()
            
            logger.info(f"工作流执行完成: thread_id={thread_id}")
            
            return Response(WorkflowStatusSerializer(execution).data)
            
        except Exception as e:
            if 'execution' in locals():
                execution.status = 'failed'
                execution.result = {'error': str(e)}
                execution.save()
            
            error_msg = f"工作流执行失败: {str(e)}"
            logger.error(error_msg)
            
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class WorkflowStatusView(APIView):
    def get(self, request, thread_id):
        try:
            execution = WorkflowExecution.objects.get(thread_id=thread_id)
            return Response(WorkflowStatusSerializer(execution).data)
        except WorkflowExecution.DoesNotExist:
            return Response({'error': '工作流不存在'}, status=status.HTTP_404_NOT_FOUND)
