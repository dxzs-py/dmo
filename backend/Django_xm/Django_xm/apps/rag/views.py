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
from rest_framework.permissions import IsAuthenticated

from django.conf import settings as django_settings
from django.http import StreamingHttpResponse

from Django_xm.utils.responses import success_response, error_response, not_found_response, validation_error_response
from Django_xm.utils.error_codes import ErrorCode
from Django_xm.apps.core.config import settings as app_cfg

from .serializers import (
    RagQuerySerializer,
    RagResponseSerializer,
    DocumentIndexSerializer,
    DocumentSerializer,
    IndexCreateSerializer,
    EmptyIndexCreateSerializer,
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
    
    从指定目录加载文档，创建向量索引，或者创建空索引。
    需要用户认证。
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = IndexCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                details=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )
        
        data = serializer.validated_data
        logger.info(f"创建索引请求：{data['name']}")
        
        try:
            manager = IndexManager()
            if manager.index_exists(data['name']) and not data.get('overwrite', False):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引已存在：{data["name"]}。使用 overwrite=true 来覆盖。',
                    http_status=status.HTTP_409_CONFLICT
                )
            
            # 检查是否提供了 directory_path
            directory_path_str = data.get('directory_path')
            if directory_path_str:
                directory_path = Path(directory_path_str)
                if not directory_path.exists():
                    return error_response(
                        code=ErrorCode.NOT_FOUND,
                        message=f'目录不存在：{directory_path_str}',
                        http_status=status.HTTP_404_NOT_FOUND
                    )

                logger.info(f"加载文档：{directory_path}")
                documents = load_documents_from_directory(str(directory_path))
                
                if not documents:
                    return error_response(
                        code=ErrorCode.INVALID_PARAMS,
                        message='目录中没有找到支持的文档',
                        http_status=status.HTTP_400_BAD_REQUEST
                    )
                
                logger.info("分块文档...")
                chunks = split_documents(
                    documents,
                    chunk_size=data.get('chunk_size'),
                    chunk_overlap=data.get('chunk_overlap'),
                )
                
                logger.info("创建 embeddings...")
                embeddings = get_embeddings()
                
                logger.info("创建向量索引...")
                vector_store = manager.create_index(
                    name=data['name'],
                    documents=chunks,
                    embeddings=embeddings,
                    description=data.get('description', ''),
                    overwrite=data.get('overwrite', False),
                )
            else:
                # 创建空索引
                logger.info("创建空索引...")
                manager.create_empty_index(
                    name=data['name'],
                    description=data.get('description', ''),
                    overwrite=data.get('overwrite', False),
                )
            
            stats = manager.get_index_stats(data['name'])
            
            logger.info(f"索引创建成功：{data['name']}")

            return success_response(
                data=IndexInfoSerializer({
                    'name': data['name'],
                    'description': data.get('description', ''),
                    'created_at': stats.get('created_at', ''),
                    'updated_at': stats.get('updated_at', ''),
                    'num_documents': stats.get('num_documents', 0),
                    'store_type': stats.get('store_type', 'faiss'),
                    'embedding_model': stats.get('embedding_model', ''),
                }).data,
                message='索引创建成功'
            )
            
        except Exception as e:
            logger.error(f"创建索引失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGEmptyIndexCreateView(APIView):
    """
    创建空索引
    
    创建一个空的向量索引，用于后续上传文档。
    需要用户认证。
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        serializer = EmptyIndexCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                details=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )
        
        data = serializer.validated_data
        index_name = data['name']
        logger.info(f"创建空索引请求：{index_name}")
        
        try:
            manager = IndexManager()
            
            # 检查索引是否已存在
            if manager.index_exists(index_name) and not data.get('overwrite', False):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引已存在：{index_name}。使用 overwrite=true 来覆盖，或者选择其他名称。',
                    http_status=status.HTTP_409_CONFLICT
                )
            
            # 创建空索引
            manager.create_empty_index(
                name=index_name,
                description=data.get('description', ''),
                overwrite=data.get('overwrite', False),
            )
            
            stats = manager.get_index_stats(index_name)
            
            logger.info(f"空索引创建成功：{index_name}")
            return success_response(
                data=IndexInfoSerializer({
                    'name': index_name,
                    'description': data.get('description', ''),
                    'created_at': stats.get('created_at', ''),
                    'updated_at': stats.get('updated_at', ''),
                    'num_documents': stats.get('num_documents', 0),
                    'store_type': stats.get('store_type', 'faiss'),
                    'embedding_model': stats.get('embedding_model', ''),
                }).data,
                message=f'索引 "{index_name}" 创建成功'
            )
            
        except Exception as e:
            logger.error(f"创建空索引失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexListView(APIView):
    """
    列出所有索引
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        try:
            manager = IndexManager()
            indexes = manager.list_indexes()

            return success_response(
                data=IndexInfoSerializer(indexes, many=True).data
            )

        except Exception as e:
            logger.error(f"列出索引失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexDetailView(APIView):
    """
    获取索引详细信息
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            stats = manager.get_index_stats(name)

            return success_response(
                data=IndexInfoSerializer(stats).data
            )

        except Exception as e:
            logger.error(f"获取索引信息失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexDeleteView(APIView):
    """
    删除索引
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            # 删除索引数据
            manager.delete_index(name)
            
            # 删除对应的上传文件
            upload_dir = Path(app_cfg.data_uploads_path) / name
            if upload_dir.exists() and upload_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(upload_dir)
                    logger.info(f"已删除索引 {name} 的上传文件目录")
                except Exception as e:
                    logger.warning(f"删除上传文件目录失败：{e}")

            return success_response(
                data={'message': f'索引已删除：{name}'},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"删除索引失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexStatsView(APIView):
    """
    获取索引统计信息
    """

    def get(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')
            
            # 检查是否为空索引
            metadata = manager._load_metadata(name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            
            if num_documents > 0:
                # 只有在有文档时才加载向量存储获取详细统计
                embeddings = get_embeddings()
                stats = manager.get_index_stats(name, embeddings)
            else:
                # 空索引只返回基本统计信息
                stats = manager.get_index_stats(name)

            return success_response(
                data=stats,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"获取统计信息失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==================== 文档管理接口 ====================

class RAGDocumentUploadView(APIView):
    """
    上传文档到索引
    """
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            if 'file' not in request.FILES:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='没有上传文件',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            uploaded_file = request.FILES['file']

            upload_dir = Path(app_cfg.data_uploads_path) / name
            upload_dir.mkdir(parents=True, exist_ok=True)

            file_path = upload_dir / uploaded_file.name
            with open(file_path, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)

            logger.info(f"加载上传的文档：{file_path}")
            documents = load_document(str(file_path))

            if not documents:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='无法加载文档，格式可能不支持',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            chunks = split_documents(documents)

            embeddings = get_embeddings()
            count = manager.add_documents(name, chunks, embeddings)

            logger.info(f"成功添加 {count} 个文档到索引 {name}")

            return success_response(
                data={'message': f'成功添加 {count} 个文档', 'count': count},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"上传文档失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentAddDirectoryView(APIView):
    """
    添加目录到索引
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            directory_path = request.data.get('directory_path')
            if not directory_path:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='directory_path 参数必填',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            directory_path = Path(directory_path)
            if not directory_path.exists():
                return not_found_response(message=f'目录不存在：{directory_path}')

            logger.info(f"加载目录：{directory_path}")
            documents = load_documents_from_directory(str(directory_path))

            if not documents:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='目录中没有找到支持的文档',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            chunks = split_documents(documents)

            embeddings = get_embeddings()
            count = manager.add_documents(name, chunks, embeddings)

            logger.info(f"成功添加 {count} 个文档到索引 {name}")

            return success_response(
                data={'message': f'成功添加 {count} 个文档', 'count': count},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"添加目录失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentListView(APIView):
    """
    获取索引下的文件列表
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            upload_dir = Path(app_cfg.data_uploads_path) / name
            
            files = []
            if upload_dir.exists():
                for item in upload_dir.iterdir():
                    if item.is_file():
                        file_stat = item.stat()
                        files.append({
                            'name': item.name,
                            'size': file_stat.st_size,
                            'uploaded_at': datetime.fromtimestamp(file_stat.st_ctime).isoformat()
                        })

            return success_response(
                data={'files': files, 'count': len(files)},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"获取文件列表失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentDeleteView(APIView):
    """
    删除索引下的文件
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, name, filename):
        try:
            manager = IndexManager()

            if not manager.index_exists(name):
                return not_found_response(message=f'索引不存在：{name}')

            upload_dir = Path(app_cfg.data_uploads_path) / name
            file_path = upload_dir / filename

            if not file_path.exists() or not file_path.is_file():
                return not_found_response(message=f'文件不存在：{filename}')

            # 删除文件
            file_path.unlink()
            
            logger.info(f"删除文件：{file_path}")

            # TODO：这里我们可以考虑重新构建索引，或者从索引中移除该文件的内容
            # 目前为了简化，我们只是删除文件，索引中仍然保留文档片段

            return success_response(
                data={'message': f'文件已删除：{filename}'},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"删除文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==================== 查询接口 ====================

class RAGQueryView(APIView):
    """
    RAG 查询（非流式）

    基于索引内容回答问题。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        data = serializer.validated_data
        logger.info(f"RAG 查询：{data['query'][:50]}...")

        try:
            manager = IndexManager()
            if not manager.index_exists(data['index_name']):
                return not_found_response(message=f'索引不存在：{data["index_name"]}')
            
            # 检查是否为空索引
            metadata = manager._load_metadata(data['index_name'])
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(
                    message="索引中暂无文档，请先上传文档后再查询",
                    errors=[]
                )

            embeddings = get_embeddings()
            vector_store = manager.load_index(data['index_name'], embeddings)

            retriever = create_retriever(vector_store, k=data.get('k', 4))

            agent = create_rag_agent(retriever)

            result = query_rag_agent(
                agent,
                data['query'],
                return_sources=data.get('return_sources', True),
            )

            logger.info("查询完成")

            return success_response(
                data=RagResponseSerializer({
                    'answer': result.get('answer', ''),
                    'sources': result.get('sources', []),
                    'success': True
                }).data,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"查询失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGSearchView(APIView):
    """
    纯检索接口（不生成回答）
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                errors=serializer.errors,
                message="数据验证失败"
            )

        data = serializer.validated_data
        logger.info(f"RAG 检索：{data['query'][:50]}...")

        try:
            manager = IndexManager()
            if not manager.index_exists(data['index_name']):
                return not_found_response(message=f'索引不存在：{data["index_name"]}')
            
            # 检查是否为空索引
            metadata = manager._load_metadata(data['index_name'])
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(
                    message="索引中暂无文档，请先上传文档后再检索",
                    errors=[]
                )

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

            return success_response(
                data=SearchResultSerializer(search_results, many=True).data,
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"检索失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
        manager = IndexManager()
        if not manager.index_exists(data['index_name']):
            return Response({
                'code': 404,
                'message': f'索引不存在：{data["index_name"]}'
            }, status=status.HTTP_404_NOT_FOUND)

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
            'code': 500,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
