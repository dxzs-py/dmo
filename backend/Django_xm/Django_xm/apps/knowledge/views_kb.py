"""
知识库管理视图

提供知识库 CRUD、文档管理、搜索等接口。
"""

import logging
from pathlib import Path
from datetime import datetime

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.core.throttling import KnowledgeRateThrottle

from Django_xm.apps.common.responses import (
    success_response, error_response, not_found_response,
)
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.apps.cache_manager.services.cache_service import (
    QueryCacheService, VectorSearchCacheService, CacheService,
)

from .models import DocumentIndex, Document, DocumentFileType
from .services.index_service import IndexManager
from .services.embedding_service import get_embeddings
from .services.document_service import load_document
from .services.splitters import split_documents
from .vector_store import search_vector_store
from .views_utils import get_user_index_name, get_original_index_name, get_file_extension, get_document_type

logger = logging.getLogger(__name__)


class KnowledgeBaseListView(APIView):
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
                    user_indexes.append({
                        'id': original_name,
                        'name': original_name,
                        'description': idx_data.get('description', ''),
                        'chunk_count': idx_data.get('num_documents', 0),
                        'created_at': idx_data.get('created_at', ''),
                        'updated_at': idx_data.get('updated_at', ''),
                    })

            return success_response(data={'items': user_indexes})
        except Exception as e:
            logger.error(f"获取知识库列表失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        name = request.data.get('name')
        description = request.data.get('description', '')

        if not name:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='知识库名称不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if manager.index_exists(user_index_name):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'知识库已存在: {name}',
                    http_status=status.HTTP_409_CONFLICT
                )

            manager.create_empty_index(name=user_index_name, description=description)

            existing = DocumentIndex.all_objects.filter(user=user, index_name=name).first()
            if existing:
                if existing.is_deleted:
                    existing.is_deleted = False
                    existing.deleted_at = None
                    existing.description = description
                    existing.save(request=request)
                    index_obj = existing
                else:
                    CacheService.delete(f"kb_list:user_{user.id}")
                    return success_response(data={
                        'id': name,
                        'name': name,
                        'description': existing.description,
                        'document_count': existing.document_count,
                        'chunk_count': 0,
                    }, message='知识库已存在')
            else:
                index_obj = DocumentIndex(
                    user=user,
                    index_name=name,
                    description=description
                )
                index_obj.save(request=request)

            CacheService.delete(f"kb_list:user_{user.id}")

            return success_response(data={
                'id': name,
                'name': name,
                'description': description,
                'document_count': 0,
                'chunk_count': 0,
            }, message='知识库创建成功')
        except Exception as e:
            logger.error(f"创建知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kb_id):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            stats = manager.get_index_stats(user_index_name)
            metadata = manager._load_metadata(user_index_name) or {}

            return success_response(data={
                'id': kb_id,
                'name': kb_id,
                'description': metadata.get('description', ''),
                'chunk_count': stats.get('num_documents', 0),
                'store_type': stats.get('store_type', ''),
                'embedding_model': stats.get('embedding_model', ''),
                'created_at': metadata.get('created_at', ''),
                'updated_at': metadata.get('updated_at', ''),
            })
        except Exception as e:
            logger.error(f"获取知识库详情失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='获取知识库详情失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def put(self, request, kb_id):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            description = request.data.get('description', '')

            metadata = manager._load_metadata(user_index_name) or {}
            metadata['description'] = description
            metadata['updated_at'] = datetime.now().isoformat()
            manager._save_metadata(user_index_name, metadata)

            index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
            if index_obj:
                index_obj.description = description
                index_obj.save(request=request)

            CacheService.delete(f"kb_list:user_{user.id}")

            return success_response(data={
                'id': kb_id,
                'name': kb_id,
                'description': description,
            }, message='知识库更新成功')
        except Exception as e:
            logger.error(f"更新知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='更新知识库失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self, request, kb_id):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            manager.delete_index(user_index_name)

            index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
            if index_obj:
                Document.objects.filter(index=index_obj).update(is_deleted=True)
                index_obj.soft_delete()

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            if upload_dir.exists() and upload_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(upload_dir)
                except Exception as e:
                    logger.warning(f"删除上传文件目录失败: {e}")

            CacheService.delete(f"kb_list:user_{user.id}")
            CacheService.delete(f"doc_list:{user_index_name}")
            QueryCacheService.invalidate_index_queries(user_index_name)
            VectorSearchCacheService.invalidate_index_queries(user_index_name)

            from Django_xm.apps.chat.models import ChatSession
            ChatSession.objects.filter(
                user=user, selected_knowledge_base=kb_id
            ).update(selected_knowledge_base='')

            return success_response(message='知识库删除成功')
        except Exception as e:
            logger.error(f"删除知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除知识库失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kb_id):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

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

            logger.info(f"返回 {len(files)} 个文档给用户 {user.username}")

            headers = {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
            }

            return success_response(
                data={'files': files, 'count': len(files)},
                headers=headers
            )
        except Exception as e:
            logger.error(f"获取文档列表失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseUploadView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, kb_id):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            files = request.FILES.getlist('files')
            if not files:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='请选择要上传的文件',
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

            index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
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
            }, message='文档上传成功')
        except Exception as e:
            logger.error(f"上传文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, kb_id, filename):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            file_path = upload_dir / filename

            if not file_path.exists() or not file_path.is_file():
                return not_found_response(message=f'文件不存在: {filename}')

            logger.info(f"删除文档: {filename} 从 {user_index_name}")

            try:
                embeddings = get_embeddings()
                removed = manager.remove_documents_by_filename(user_index_name, embeddings, filename)
                logger.info(f"从向量索引中删除 {removed} 个文档块")
            except Exception as ve:
                logger.warning(f"从向量索引中删除文档失败: {ve}")

            index_obj = DocumentIndex.objects.filter(user=user, index_name=kb_id).first()
            if index_obj:
                doc = Document.objects.filter(index=index_obj, filename=filename).first()
                if doc:
                    doc.soft_delete()
                index_obj.document_count = max(0, index_obj.document_count - 1)
                index_obj.save(request=request)

            file_path.unlink()
            logger.info(f"文件已从磁盘删除: {file_path}")

            metadata = manager._load_metadata(user_index_name) or {}
            metadata['updated_at'] = datetime.now().isoformat()
            if 'num_documents' in metadata:
                metadata['num_documents'] = max(0, metadata['num_documents'] - 1)
            manager._save_metadata(user_index_name, metadata)

            CacheService.delete(f"doc_list:{user_index_name}")
            QueryCacheService.invalidate_index_queries(user_index_name)
            VectorSearchCacheService.invalidate_index_queries(user_index_name)

            return success_response(data={'message': f'文件已删除: {filename}'}, message='操作成功')
        except Exception as e:
            logger.error(f"删除文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除文档失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, kb_id):
        query = request.data.get('query', '')
        top_k = request.data.get('top_k', 5)

        if not query:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='查询内容不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = request.user
            user_index_name = get_user_index_name(user, kb_id)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'知识库不存在: {kb_id}')

            embeddings = get_embeddings()
            vector_store = manager.load_index(user_index_name, embeddings)

            cached_results = VectorSearchCacheService.get_cached_search(query, user_index_name, top_k)
            if cached_results is not None:
                logger.info("向量搜索缓存命中")
                search_results = cached_results
            else:
                results = search_vector_store(vector_store, query, k=top_k)
                search_results = []
                for doc, score in results:
                    item = {
                        'content': doc.page_content,
                        'source': doc.metadata.get('source', ''),
                        'score': float(score) if score is not None else 0,
                    }
                    search_results.append(item)
                VectorSearchCacheService.cache_search_result(query, search_results, user_index_name, top_k)

            return success_response(data={'results': search_results})
        except Exception as e:
            logger.error(f"检索测试失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
