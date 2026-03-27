import logging
import time
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import ResearchStartSerializer, ResearchTaskSerializer
from .models import ResearchTask
from Django_xm.libs.langchain_core.deep_research import create_deep_research_agent
import logging

logger = logging.getLogger(__name__)

class DeepResearchStartView(APIView):
    def post(self, request):
        serializer = ResearchStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        task_id = data.get('task_id') or f"dr_{int(time.time() * 1000)}"
        
        logger.info(f"启动深度研究任务: task_id={task_id}, query={data['query'][:50]}...")
        
        try:
            task = ResearchTask.objects.create(
                task_id=task_id,
                query=data['query'],
                status='running',
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False)
            )
            
            agent = create_deep_research_agent(
                thread_id=task_id,
                enable_web_search=data.get('enable_web_search', True),
                enable_doc_analysis=data.get('enable_doc_analysis', False)
            )
            result = agent.research(data['query'])
            
            task.status = 'completed'
            task.final_report = result.get('final_report', '')
            task.save()
            
            logger.info(f"深度研究任务完成: task_id={task_id}")
            
            return Response(ResearchTaskSerializer(task).data)
            
        except Exception as e:
            if 'task' in locals():
                task.status = 'failed'
                task.final_report = f"错误: {str(e)}"
                task.save()
            
            error_msg = f"深度研究任务失败: {str(e)}"
            logger.error(error_msg)
            
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DeepResearchStatusView(APIView):
    def get(self, request, task_id):
        try:
            task = ResearchTask.objects.get(task_id=task_id)
            return Response(ResearchTaskSerializer(task).data)
        except ResearchTask.DoesNotExist:
            return Response({'error': '研究任务不存在'}, status=status.HTTP_404_NOT_FOUND)
