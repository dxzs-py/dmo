import logging
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import (
    RagQuerySerializer, RagResponseSerializer,
    DocumentIndexSerializer, DocumentSerializer
)
from .models import DocumentIndex, Document
from Django_xm.libs.langchain_rag.rag_agent import create_rag_agent, query_rag_agent
from Django_xm.libs.langchain_rag.index_manager import IndexManager
import logging

logger = logging.getLogger(__name__)

class RagQueryView(APIView):
    def post(self, request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        logger.info(f"收到RAG查询请求: index={data['index_name']}, query={data['query'][:50]}...")
        
        try:
            manager = IndexManager()
            vector_store = manager.load_index(data['index_name'])
            
            if data.get('use_rag_agent', True):
                agent = create_rag_agent(vector_store, k=data.get('k', 4))
                result = query_rag_agent(agent, data['query'])
            else:
                from Django_xm.libs.langchain_rag.retrievers import create_retriever
                retriever = create_retriever(vector_store, k=data.get('k', 4))
                docs = retriever.get_relevant_documents(data['query'])
                result = {
                    'answer': '检索完成',
                    'sources': [{'content': doc.page_content, 'metadata': doc.metadata} for doc in docs]
                }
            
            logger.info(f"RAG查询处理完成")
            
            return Response(RagResponseSerializer({
                'answer': result.get('answer', ''),
                'sources': result.get('sources', []),
                'success': True
            }).data)
            
        except Exception as e:
            error_msg = f"处理RAG查询时出错: {str(e)}"
            logger.error(error_msg)
            
            return Response(RagResponseSerializer({
                'answer': '抱歉，处理您的请求时出现错误。',
                'sources': [],
                'success': False,
                'error': str(e)
            }).data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DocumentIndexListView(APIView):
    def get(self, request):
        indexes = DocumentIndex.objects.all()
        return Response(DocumentIndexSerializer(indexes, many=True).data)

class DocumentListView(APIView):
    def get(self, request, index_id):
        try:
            index = DocumentIndex.objects.get(id=index_id)
            documents = index.documents.all()
            return Response(DocumentSerializer(documents, many=True).data)
        except DocumentIndex.DoesNotExist:
            return Response({'error': '索引不存在'}, status=status.HTTP_404_NOT_FOUND)
