"""
RAG 索引/文档/查询视图

提供 RAG 索引管理、文档管理、查询和检索接口。
"""

import json
import logging
from pathlib import Path
from datetime import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view

from Django_xm.apps.core.throttling import KnowledgeRateThrottle
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated

from django.http import StreamingHttpResponse

from Django_xm.apps.common.sse_utils import sse_response

from Django_xm.apps.common.responses import (
    success_response, error_response, not_found_response, validation_error_response,
)
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.apps.cache_manager.services.cache_service import (
    QueryCacheService, VectorSearchCacheService, CacheService,
)

from .serializers import (
    RagQuerySerializer,
    RagResponseSerializer,
    IndexCreateSerializer,
    EmptyIndexCreateSerializer,
    IndexInfoSerializer,
    SearchRequestSerializer,
    SearchResultSerializer,
)
from .models import DocumentIndex, Document
from .services.rag_agent import create_rag_agent, query_rag_agent
from .services.index_service import IndexManager
from .services.embedding_service import get_embeddings
from .services.document_service import load_document, load_documents_from_directory
from .services.splitters import split_documents
from .services.retrieval_service import create_retriever
from .vector_store import search_vector_store
from .views_utils import get_user_index_name, get_original_index_name, get_file_extension, get_document_type

logger = logging.getLogger(__name__)


def _save_or_restore_index_record(user, original_name, description='', document_count=0, overwrite=False, request=None):
    existing = DocumentIndex.all_objects.filter(user=user, index_name=original_name).first()
    if existing:
        if existing.is_deleted:
            existing.is_deleted = False
            existing.deleted_at = None
            existing.description = description
            if document_count:
                existing.document_count = document_count
            existing.save(request=request)
        elif overwrite:
            existing.description = description or existing.description
            if document_count:
                existing.document_count = document_count
            existing.save(request=request)
        else:
            return None
    else:
        index_obj = DocumentIndex(
            user=user,
            index_name=original_name,
            description=description,
            document_count=document_count,
        )
        index_obj.save(request=request)
    return True


class RAGIndexCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = IndexCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='数据验证失败',
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        user = request.user
        original_name = data['name']
        user_index_name = get_user_index_name(user, original_name)

        logger.info(f"创建索引请求: {original_name}")

        try:
            manager = IndexManager()
            if manager.index_exists(user_index_name) and not data.get('overwrite', False):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引已存在: {original_name}。使用 overwrite=true 来覆盖。',
                    http_status=status.HTTP_409_CONFLICT
                )

            directory_path_str = data.get('directory_path')
            documents = []
            if directory_path_str:
                directory_path = Path(directory_path_str)
                if not directory_path.exists():
                    return error_response(
                        code=ErrorCode.NOT_FOUND,
                        message=f'目录不存在: {directory_path_str}',
                        http_status=status.HTTP_404_NOT_FOUND
                    )

                logger.info(f"加载文档: {directory_path}")
                documents = load_documents_from_directory(str(directory_path))

                if not documents:
                    return error_response(
                        code=ErrorCode.INVALID_PARAMS,
                        message='目录中没有找到支持的文档',
                        http_status=status.HTTP_400_BAD_REQUEST
                    )

                chunks = split_documents(
                    documents,
                    chunk_size=data.get('chunk_size'),
                    chunk_overlap=data.get('chunk_overlap'),
                )

                embeddings = get_embeddings()
                manager.create_index(
                    name=user_index_name,
                    documents=chunks,
                    embeddings=embeddings,
                    description=data.get('description', ''),
                    overwrite=data.get('overwrite', False),
                )
            else:
                manager.create_empty_index(
                    name=user_index_name,
                    description=data.get('description', ''),
                    overwrite=data.get('overwrite', False),
                )

            result = _save_or_restore_index_record(
                user=user,
                original_name=original_name,
                description=data.get('description', ''),
                document_count=len(documents),
                overwrite=data.get('overwrite', False),
                request=request,
            )
            if result is None:
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引 "{original_name}" 已存在，请使用其他名称或先删除已有索引',
                    http_status=status.HTTP_409_CONFLICT
                )

            CacheService.delete(f"kb_list:user_{user.id}")

            stats = manager.get_index_stats(user_index_name)
            return success_response(
                data=IndexInfoSerializer({
                    'name': original_name,
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
            logger.error(f"创建索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGEmptyIndexCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EmptyIndexCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message='数据验证失败',
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        user = request.user
        original_name = data['name']
        user_index_name = get_user_index_name(user, original_name)

        logger.info(f"创建空索引请求: {original_name}")

        try:
            manager = IndexManager()
            if manager.index_exists(user_index_name) and not data.get('overwrite', False):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引已存在: {original_name}。使用 overwrite=true 来覆盖。',
                    http_status=status.HTTP_409_CONFLICT
                )

            manager.create_empty_index(
                name=user_index_name,
                description=data.get('description', ''),
                overwrite=data.get('overwrite', False),
            )

            result = _save_or_restore_index_record(
                user=user,
                original_name=original_name,
                description=data.get('description', ''),
                overwrite=data.get('overwrite', False),
                request=request,
            )
            if result is None:
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引 "{original_name}" 已存在，请使用其他名称或先删除已有索引',
                    http_status=status.HTTP_409_CONFLICT
                )

            CacheService.delete(f"kb_list:user_{user.id}")

            stats = manager.get_index_stats(user_index_name)
            return success_response(
                data=IndexInfoSerializer({
                    'name': original_name,
                    'description': data.get('description', ''),
                    'created_at': stats.get('created_at', ''),
                    'updated_at': stats.get('updated_at', ''),
                    'num_documents': stats.get('num_documents', 0),
                    'store_type': stats.get('store_type', 'faiss'),
                    'embedding_model': stats.get('embedding_model', ''),
                }).data,
                message=f'索引 "{original_name}" 创建成功'
            )

        except Exception as e:
            logger.error(f"创建空索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user = request.user

            deleted_index_names = set(
                DocumentIndex.all_objects.filter(
                    user=user, is_deleted=True
                ).values_list('index_name', flat=True)
            )

            manager = IndexManager()
            all_indexes = manager.list_indexes()

            user_indexes = []
            for idx_data in all_indexes:
                name = idx_data.get('name', '')
                if name.startswith(f"user_{user.id}_"):
                    original_name = get_original_index_name(name)
                    if original_name in deleted_index_names:
                        continue
                    user_indexes.append({**idx_data, 'name': original_name})

            return success_response(
                data=IndexInfoSerializer(user_indexes, many=True).data
            )

        except Exception as e:
            logger.error(f"列出索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            stats = manager.get_index_stats(user_index_name)
            return success_response(data=IndexInfoSerializer(stats).data)

        except Exception as e:
            logger.error(f"获取索引信息失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            manager.delete_index(user_index_name)

            index_obj = DocumentIndex.objects.filter(user=user, index_name=name).first()
            if index_obj:
                Document.objects.filter(index=index_obj).update(is_deleted=True)
                index_obj.soft_delete()

            CacheService.delete(f"kb_list:user_{user.id}")

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            if upload_dir.exists() and upload_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(upload_dir)
                except Exception as e:
                    logger.warning(f"删除上传文件目录失败: {e}")

            from Django_xm.apps.chat.models import ChatSession
            ChatSession.objects.filter(
                user=user, selected_knowledge_base=name
            ).update(selected_knowledge_base='')

            return success_response(
                data={'message': f'索引已删除: {name}'},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"删除索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除索引失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGIndexStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0

            if num_documents > 0:
                embeddings = get_embeddings()
                stats = manager.get_index_stats(user_index_name, embeddings)
            else:
                stats = manager.get_index_stats(user_index_name)

            return success_response(data=stats, message='操作成功')

        except Exception as e:
            logger.error(f"获取统计信息失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentUploadView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            files = request.FILES.getlist('files')
            if not files and 'file' in request.FILES:
                files = [request.FILES['file']]
            if not files:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='没有上传文件',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            upload_dir.mkdir(parents=True, exist_ok=True)

            all_documents = []
            saved_files = []

            for uploaded_file in files:
                file_path = upload_dir / uploaded_file.name
                with open(file_path, 'wb') as f:
                    for chunk in uploaded_file.chunks():
                        f.write(chunk)

                docs = load_document(str(file_path))
                all_documents.extend(docs)
                saved_files.append({
                    'name': uploaded_file.name,
                    'size': file_path.stat().st_size,
                })

            if not all_documents:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='未能从上传文件中提取内容',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            chunks = split_documents(all_documents)
            embeddings = get_embeddings()
            count = manager.add_documents(user_index_name, chunks, embeddings)

            index_obj = DocumentIndex.objects.filter(user=user, index_name=name).first()
            if index_obj:
                for file_info in saved_files:
                    ext = get_file_extension(file_info['name'])
                    doc_type = get_document_type(ext)
                    doc = Document(
                        index=index_obj,
                        filename=file_info['name'],
                        file_path=str(upload_dir / file_info['name']),
                        file_type=doc_type,
                        file_size=file_info['size'],
                        chunk_count=len(chunks) // len(saved_files) if saved_files else 0
                    )
                    doc.save()
                index_obj.document_count += len(saved_files)
                index_obj.save(request=request)

            CacheService.delete(f"doc_list:{user_index_name}")
            QueryCacheService.invalidate_index_queries(user_index_name)
            VectorSearchCacheService.invalidate_index_queries(user_index_name)

            return success_response(data={
                'documents_uploaded': len(saved_files),
                'chunks_created': count,
                'files': saved_files,
            }, message='操作成功')

        except Exception as e:
            logger.error(f"上传文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentAddDirectoryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            directory_path = request.data.get('directory_path')
            if not directory_path:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='directory_path 参数必填',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            directory_path = Path(directory_path)
            if not directory_path.exists():
                return not_found_response(message=f'目录不存在: {directory_path}')

            logger.info(f"加载目录: {directory_path}")
            documents = load_documents_from_directory(str(directory_path))

            if not documents:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='目录中没有找到支持的文档',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            chunks = split_documents(documents)
            embeddings = get_embeddings()
            count = manager.add_documents(user_index_name, chunks, embeddings)

            return success_response(
                data={'message': f'成功添加 {count} 个文档', 'count': count},
                message='操作成功'
            )

        except Exception as e:
            logger.error(f"添加目录失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)

            cache_key = f"doc_list:{user_index_name}"
            cached = CacheService.get(cache_key)
            if cached is not None:
                logger.info("文档列表缓存命中")
                return success_response(data=cached)

            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            files = []
            if upload_dir.exists():
                for item in upload_dir.iterdir():
                    if item.is_file():
                        stat = item.stat()
                        files.append({
                            'name': item.name,
                            'size': stat.st_size,
                            'uploaded_at': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        })

            result = {'files': files, 'count': len(files)}
            CacheService.set(cache_key, result, ttl=60)
            return success_response(data=result, message='操作成功')

        except Exception as e:
            logger.error(f"获取文件列表失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, name, filename):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            file_path = upload_dir / filename

            if not file_path.exists() or not file_path.is_file():
                return not_found_response(message=f'文件不存在: {filename}')

            try:
                embeddings = get_embeddings()
                removed = manager.remove_documents_by_filename(user_index_name, embeddings, filename)
                logger.info(f"从向量索引中删除 {removed} 个文档块")
            except Exception as ve:
                logger.warning(f"从向量索引中删除文档失败: {ve}")

            index_obj = DocumentIndex.objects.filter(user=user, index_name=name).first()
            if index_obj:
                doc = Document.objects.filter(index=index_obj, filename=filename).first()
                if doc:
                    doc.soft_delete()
                index_obj.document_count = max(0, index_obj.document_count - 1)
                index_obj.save(request=request)

            file_path.unlink()
            logger.info(f"删除文件: {file_path}")

            CacheService.delete(f"doc_list:{user_index_name}")
            QueryCacheService.invalidate_index_queries(user_index_name)
            VectorSearchCacheService.invalidate_index_queries(user_index_name)

            return success_response(data={'message': f'文件已删除: {filename}'}, message='操作成功')

        except Exception as e:
            logger.error(f"删除文件失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除文件失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGQueryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message='数据验证失败')

        data = serializer.validated_data
        user = request.user
        index_name = data['index_name']
        user_index_name = get_user_index_name(user, index_name)
        k = data.get('k', 4)

        logger.info(f"RAG 查询: {data['query'][:50]}...")

        try:
            cached_result = QueryCacheService.get_cached_query(
                data['query'], user_index_name, k
            )
            if cached_result is not None:
                logger.info("RAG 查询缓存命中")
                return success_response(
                    data=RagResponseSerializer({
                        'answer': cached_result['result'].get('answer', ''),
                        'sources': cached_result['result'].get('sources', []),
                        'success': True,
                        'cached': True,
                    }).data,
                    message='操作成功'
                )

            manager = IndexManager()
            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {index_name}')

            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(
                    message='索引中暂无文档，请先上传文档后再查询',
                    errors=[]
                )

            embeddings = get_embeddings()
            vector_store = manager.load_index(user_index_name, embeddings)
            retriever = create_retriever(vector_store, k=k)
            agent = create_rag_agent(retriever)
            result = query_rag_agent(
                agent,
                data['query'],
                return_sources=data.get('return_sources', True),
            )

            QueryCacheService.cache_query_result(
                data['query'], result, user_index_name, k
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
            logger.error(f"查询失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message='数据验证失败')

        data = serializer.validated_data
        user = request.user
        index_name = data['index_name']
        user_index_name = get_user_index_name(user, index_name)
        k = data.get('k', 4)
        score_threshold = data.get('score_threshold')

        logger.info(f"RAG 检索: {data['query'][:50]}...")

        try:
            cached_results = VectorSearchCacheService.get_cached_search(
                data['query'], user_index_name, k
            )
            if cached_results is not None:
                logger.info("向量搜索缓存命中")
                return success_response(
                    data=SearchResultSerializer(cached_results, many=True).data,
                    message='操作成功'
                )

            manager = IndexManager()
            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {index_name}')

            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(
                    message='索引中暂无文档，请先上传文档后再检索',
                    errors=[]
                )

            embeddings = get_embeddings()
            vector_store = manager.load_index(user_index_name, embeddings)

            results = search_vector_store(vector_store, data['query'], k=k, score_threshold=score_threshold)

            search_results = []
            for doc, score in results:
                item = {
                    'content': doc.page_content,
                    'metadata': doc.metadata,
                }
                if score is not None:
                    item['score'] = float(score)
                search_results.append(item)

            VectorSearchCacheService.cache_search_result(
                data['query'], search_results, user_index_name, k
            )

            return success_response(data=SearchResultSerializer(search_results, many=True).data, message='操作成功')

        except Exception as e:
            logger.error(f"检索失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['POST'])
def rag_query_stream(request):
    if not request.user or not request.user.is_authenticated:
        return Response({'code': 401, 'message': '未登录或登录已过期'}, status=status.HTTP_401_UNAUTHORIZED)

    serializer = RagQuerySerializer(data=request.data)
    if not serializer.is_valid():
        return Response({
            'code': 400,
            'message': '数据验证失败',
            'details': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    user = request.user
    index_name = data['index_name']
    user_index_name = get_user_index_name(user, index_name)

    logger.info(f"RAG 流式查询: {data['query'][:50]}...")

    try:
        manager = IndexManager()
        if not manager.index_exists(user_index_name):
            return Response({
                'code': 404,
                'message': f'索引不存在: {index_name}'
            }, status=status.HTTP_404_NOT_FOUND)

        embeddings = get_embeddings()
        vector_store = manager.load_index(user_index_name, embeddings)
        retriever = create_retriever(vector_store, k=data.get('k', 4))
        agent = create_rag_agent(retriever, streaming=True)

        def event_stream():
            try:
                yield f"data: {json.dumps({'type': 'start', 'message': '开始生成...'})}\n\n"
                full_response = ""
                for chunk in agent.stream(
                    {"messages": [{"role": "user", "content": data['query']}]},
                    stream_mode="messages",
                ):
                    if not isinstance(chunk, tuple) or len(chunk) < 2:
                        continue
                    msg, metadata = chunk[0], chunk[1]
                    msg_type = getattr(msg, 'type', '')
                    if msg_type in ('human', 'tool'):
                        continue
                    if not hasattr(msg, 'content') or not msg.content:
                        continue
                    content = str(msg.content)
                    if content.strip():
                        full_response += content
                        yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
                logger.info(f"[RAG] 流式生成完成, total_len={len(full_response)}")
                yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'})}\n\n"
            except Exception as e:
                logger.error(f"[RAG] 流式生成异常: {e}", exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return sse_response(event_stream())

    except Exception as e:
        logger.error(f"流式查询失败: {e}", exc_info=True)
        return Response({
            'code': 500,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
