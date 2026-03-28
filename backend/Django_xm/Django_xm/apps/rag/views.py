"""
RAG API Views
提供 RAG 相关的 HTTP 接口：
- 索引管理（创建、列表、删除、统计）
- 文档管理（上传、添加目录）
- 查询接口（RAG 问答、纯检索）
"""

import os
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.parsers import MultiPartParser, FormParser

from django.conf import settings as django_settings
from django.http import StreamingHttpResponse

from .serializers import (
    RagQuerySerializer,
    RagResponseSerializer,
    DocumentIndexSerializer,
    DocumentSerializer,
    IndexCreateSerializer,
    IndexInfoSerializer,
    SearchRequestSerializer,
    SearchResultSerializer,
)
from .models import DocumentIndex, Document
from .rag_agent import create_rag_agent, query_rag_agent
from .index_manager import IndexManager
from .embeddings import get_embeddings
from .loaders import load_document, load_documents_from_directory
from .splitters import split_documents
from .retrievers import create_retriever
from .vector_stores import search_vector_store

logger = logging.getLogger(__name__)


# ==================== 索引管理接口 ====================

class RAGIndexCreateView(APIView):
    """
    创建新索引
    
    从指定目录加载文档，创建向量索引。
    """
    
    def post(self, request):
        serializer = IndexCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        logger.info(f"📝 创建索引请求：{data['name']}")
        
        try:
            # 检查目录是否存在
            directory_path = Path(data['directory_path'])
            if not directory_path.exists():
                return Response({
                    'error': f'目录不存在：{data["directory_path"]}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 检查索引是否已存在
            manager = IndexManager()
            if manager.index_exists(data['name']) and not data.get('overwrite', False):
                return Response({
                    'error': f'索引已存在：{data["name"]}。使用 overwrite=true 来覆盖。'
                }, status=status.HTTP_409_CONFLICT)
            
            # 加载文档
            logger.info(f"📂 加载文档：{directory_path}")
            documents = load_documents_from_directory(str(directory_path))
            
            if not documents:
                return Response({
                    'error': '目录中没有找到支持的文档'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 分块文档
            logger.info("✂️  分块文档...")
            chunks = split_documents(
                documents,
                chunk_size=data.get('chunk_size'),
                chunk_overlap=data.get('chunk_overlap'),
            )
            
            # 创建 embeddings
            logger.info("🔢 创建 embeddings...")
            embeddings = get_embeddings()
            
            # 创建索引
            logger.info("🗄️  创建向量索引...")
            vector_store = manager.create_index(
                name=data['name'],
                documents=chunks,
                embeddings=embeddings,
                description=data.get('description', ''),
                overwrite=data.get('overwrite', False),
            )
            
            # 获取索引信息
            stats = manager.get_index_stats(data['name'])
            
            logger.info(f"✅ 索引创建成功：{data['name']}")
            
            return Response(IndexInfoSerializer({
                'name': data['name'],
                'description': data.get('description', ''),
                'created_at': stats.get('created_at', ''),
                'updated_at': stats.get('updated_at', ''),
                'num_documents': stats.get('num_documents', 0),
                'store_type': stats.get('store_type', 'faiss'),
                'embedding_model': stats.get('embedding_model', ''),
            }).data)
            
        except Exception as e:
            logger.error(f"❌ 创建索引失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGIndexListView(APIView):
    """
    列出所有索引
    """
    
    def get(self, request):
        try:
            manager = IndexManager()
            indexes = manager.list_indexes()
            
            return Response(IndexInfoSerializer(indexes, many=True).data)
            
        except Exception as e:
            logger.error(f"❌ 列出索引失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGIndexDetailView(APIView):
    """
    获取索引详细信息
    """
    
    def get(self, request, name):
        try:
            manager = IndexManager()
            
            if not manager.index_exists(name):
                return Response({
                    'error': f'索引不存在：{name}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            stats = manager.get_index_stats(name)
            
            return Response(IndexInfoSerializer(stats).data)
            
        except Exception as e:
            logger.error(f"❌ 获取索引信息失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGIndexDeleteView(APIView):
    """
    删除索引
    """
    
    def delete(self, request, name):
        try:
            manager = IndexManager()
            
            if not manager.index_exists(name):
                return Response({
                    'error': f'索引不存在：{name}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            manager.delete_index(name)
            
            return Response({
                'message': f'索引已删除：{name}'
            })
            
        except Exception as e:
            logger.error(f"❌ 删除索引失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGIndexStatsView(APIView):
    """
    获取索引统计信息
    """
    
    def get(self, request, name):
        try:
            manager = IndexManager()
            
            if not manager.index_exists(name):
                return Response({
                    'error': f'索引不存在：{name}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            embeddings = get_embeddings()
            stats = manager.get_index_stats(name, embeddings)
            
            return Response(stats)
            
        except Exception as e:
            logger.error(f"❌ 获取统计信息失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== 文档管理接口 ====================

class RAGDocumentUploadView(APIView):
    """
    上传文档到索引
    """
    parser_classes = (MultiPartParser, FormParser)
    
    def post(self, request, name):
        try:
            manager = IndexManager()
            
            if not manager.index_exists(name):
                return Response({
                    'error': f'索引不存在：{name}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 检查是否有上传文件
            if 'file' not in request.FILES:
                return Response({
                    'error': '没有上传文件'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            uploaded_file = request.FILES['file']
            
            # 保存上传文件
            upload_dir = Path(django_settings.MEDIA_ROOT) / 'rag_uploads' / name
            upload_dir.mkdir(parents=True, exist_ok=True)
            
            file_path = upload_dir / uploaded_file.name
            with open(file_path, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
            
            # 加载文档
            logger.info(f"📂 加载上传的文档：{file_path}")
            documents = load_document(str(file_path))
            
            if not documents:
                return Response({
                    'error': '无法加载文档，格式可能不支持'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 分块文档
            chunks = split_documents(documents)
            
            # 添加到索引
            embeddings = get_embeddings()
            count = manager.add_documents(name, chunks, embeddings)
            
            logger.info(f"✅ 成功添加 {count} 个文档到索引 {name}")
            
            return Response({
                'message': f'成功添加 {count} 个文档',
                'count': count
            })
            
        except Exception as e:
            logger.error(f"❌ 上传文档失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGDocumentAddDirectoryView(APIView):
    """
    添加目录到索引
    """
    
    def post(self, request, name):
        try:
            manager = IndexManager()
            
            if not manager.index_exists(name):
                return Response({
                    'error': f'索引不存在：{name}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            directory_path = request.data.get('directory_path')
            if not directory_path:
                return Response({
                    'error': 'directory_path 参数必填'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            directory_path = Path(directory_path)
            if not directory_path.exists():
                return Response({
                    'error': f'目录不存在：{directory_path}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 加载文档
            logger.info(f"📂 加载目录：{directory_path}")
            documents = load_documents_from_directory(str(directory_path))
            
            if not documents:
                return Response({
                    'error': '目录中没有找到支持的文档'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # 分块文档
            chunks = split_documents(documents)
            
            # 添加到索引
            embeddings = get_embeddings()
            count = manager.add_documents(name, chunks, embeddings)
            
            logger.info(f"✅ 成功添加 {count} 个文档到索引 {name}")
            
            return Response({
                'message': f'成功添加 {count} 个文档',
                'count': count
            })
            
        except Exception as e:
            logger.error(f"❌ 添加目录失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== 查询接口 ====================

class RAGQueryView(APIView):
    """
    RAG 查询（非流式）
    
    基于索引内容回答问题。
    """
    
    def post(self, request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        logger.info(f"🔍 RAG 查询：{data['query'][:50]}...")
        
        try:
            # 检查索引是否存在
            manager = IndexManager()
            if not manager.index_exists(data['index_name']):
                return Response({
                    'error': f'索引不存在：{data["index_name"]}'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # 加载索引
            embeddings = get_embeddings()
            vector_store = manager.load_index(data['index_name'], embeddings)
            
            # 创建检索器
            retriever = create_retriever(vector_store, k=data.get('k', 4))
            
            # 创建 RAG Agent
            agent = create_rag_agent(retriever)
            
            # 查询
            result = query_rag_agent(
                agent,
                data['query'],
                return_sources=data.get('return_sources', True),
            )
            
            logger.info("✅ 查询完成")
            
            return Response(RagResponseSerializer({
                'answer': result.get('answer', ''),
                'sources': result.get('sources', []),
                'success': True
            }).data)
            
        except Exception as e:
            logger.error(f"❌ 查询失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RAGSearchView(APIView):
    """
    纯检索接口（不生成回答）
    """

    def post(self, request):
        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        logger.info(f"🔍 RAG 检索：{data['query'][:50]}...")

        try:
            manager = IndexManager()
            if not manager.index_exists(data['index_name']):
                return Response({
                    'error': f'索引不存在：{data["index_name"]}'
                }, status=status.HTTP_404_NOT_FOUND)

            embeddings = get_embeddings()
            vector_store = manager.load_index(data['index_name'], embeddings)

            k = data.get('k', 4)
            score_threshold = data.get('score_threshold')

            results = search_vector_store(
                vector_store,
                data['query'],
                k=k,
                score_threshold=score_threshold,
            )

            search_results = []
            for doc, score in results:
                result_item = {
                    'content': doc.page_content,
                    'metadata': doc.metadata,
                }
                if score is not None:
                    result_item['score'] = float(score)
                search_results.append(result_item)

            return Response(SearchResultSerializer(search_results, many=True).data)

        except Exception as e:
            logger.error(f"❌ 检索失败：{e}", exc_info=True)
            return Response({
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def rag_query_stream(request):
    """
    RAG 查询（流式）
    
    使用 Server-Sent Events (SSE) 返回流式响应。
    """
    serializer = RagQuerySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    logger.info(f"🔍 RAG 流式查询：{data['query'][:50]}...")
    
    try:
        # 检查索引是否存在
        manager = IndexManager()
        if not manager.index_exists(data['index_name']):
            return Response({
                'error': f'索引不存在：{data["index_name"]}'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # 加载索引
        embeddings = get_embeddings()
        vector_store = manager.load_index(data['index_name'], embeddings)
        
        # 创建检索器
        retriever = create_retriever(vector_store, k=data.get('k', 4))
        
        # 创建 RAG Agent
        agent = create_rag_agent(retriever, streaming=True)
        
        # 流式查询
        def event_stream():
            try:
                # 发送开始事件
                yield f"data: {json.dumps({'type': 'start', 'message': '开始生成...'})}\n\n"
                
                # 流式获取响应
                full_response = ""
                for chunk in agent.stream({"messages": [{"role": "user", "content": data['query']}]}):
                    if isinstance(chunk, dict) and "messages" in chunk:
                        messages = chunk["messages"]
                        if messages:
                            content = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])
                            if content:
                                full_response += content
                                yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
                
                # 发送结束事件
                yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'})}\n\n"
                
            except Exception as e:
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
        logger.error(f"❌ 流式查询失败：{e}", exc_info=True)
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
