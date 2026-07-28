"""提示缓存 API 视图

提供提示缓存的完整 CRUD + 测试 + 排序接口。
所有查询强制 filter(user=request.user)，确保用户隔离。
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Django_xm.apps.context_manager.models import PromptCache
from Django_xm.apps.context_manager.serializers import PromptCacheSerializer
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class PromptCacheListView(APIView):
    """提示缓存列表 + 创建"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """获取当前用户的提示缓存列表"""
        qs = PromptCache.objects.filter(user=request.user)
        cache_type = request.query_params.get('cache_type')
        if cache_type:
            qs = qs.filter(cache_type=cache_type)
        serializer = PromptCacheSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        """创建提示缓存"""
        serializer = PromptCacheSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 计算 token 数（粗略估算：中文约 1.5 字符/token，英文约 4 字符/token）
        content = serializer.validated_data.get('content', '')
        token_count = len(content) // 3

        cache = serializer.save(user=request.user, token_count=token_count)
        return Response(
            PromptCacheSerializer(cache).data,
            status=status.HTTP_201_CREATED,
        )


class PromptCacheDetailView(APIView):
    """提示缓存详情 + 更新 + 删除"""
    permission_classes = [IsAuthenticated]

    def _get_object(self, request, pk):
        try:
            return PromptCache.objects.get(pk=pk, user=request.user)
        except PromptCache.DoesNotExist:
            return None

    def get(self, request, pk):
        cache = self._get_object(request, pk)
        if not cache:
            return Response({'detail': '未找到'}, status=status.HTTP_404_NOT_FOUND)
        serializer = PromptCacheSerializer(cache)
        return Response(serializer.data)

    def put(self, request, pk):
        cache = self._get_object(request, pk)
        if not cache:
            return Response({'detail': '未找到'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PromptCacheSerializer(cache, data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 重新计算 token 数
        content = serializer.validated_data.get('content', cache.content)
        token_count = len(content) // 3

        cache = serializer.save(token_count=token_count)
        return Response(PromptCacheSerializer(cache).data)

    def patch(self, request, pk):
        """部分更新（仅更新传入的字段，未传入字段保留原值）"""
        cache = self._get_object(request, pk)
        if not cache:
            return Response({'detail': '未找到'}, status=status.HTTP_404_NOT_FOUND)

        serializer = PromptCacheSerializer(cache, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # 如果更新了 content，重新计算 token 数
        if 'content' in serializer.validated_data:
            token_count = len(serializer.validated_data['content']) // 3
            cache = serializer.save(token_count=token_count)
        else:
            cache = serializer.save()

        return Response(PromptCacheSerializer(cache).data)

    def delete(self, request, pk):
        cache = self._get_object(request, pk)
        if not cache:
            return Response({'detail': '未找到'}, status=status.HTTP_404_NOT_FOUND)
        cache.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PromptCacheTestView(APIView):
    """提示缓存模板测试"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """渲染模板变量，返回预览结果"""
        try:
            cache = PromptCache.objects.get(pk=pk, user=request.user)
        except PromptCache.DoesNotExist:
            return Response({'detail': '未找到'}, status=status.HTTP_404_NOT_FOUND)

        # 获取变量值
        variables = request.data.get('variables', {})

        # 渲染模板
        rendered = cache.content
        for var_def in cache.variables:
            var_name = var_def.get('name', '')
            default_val = var_def.get('default', '')
            value = variables.get(var_name, default_val)
            rendered = rendered.replace(f'{{{{{var_name}}}}}', str(value))

        return Response({
            'rendered': rendered,
            'token_count': len(rendered) // 3,
            'variables_used': list(variables.keys()),
        })


class PromptCacheReorderView(APIView):
    """批量排序"""
    permission_classes = [IsAuthenticated]

    def put(self, request):
        """批量更新排序

        请求体: { items: [{ id: 1, sort_order: 0 }, { id: 2, sort_order: 1 }, ...] }
        """
        items = request.data.get('items', [])
        if not items:
            return Response({'detail': 'items 不能为空'}, status=status.HTTP_400_BAD_REQUEST)

        updated = 0
        for item in items:
            pk = item.get('id')
            sort_order = item.get('sort_order', 0)
            if pk is not None:
                updated += PromptCache.objects.filter(
                    pk=pk, user=request.user
                ).update(sort_order=sort_order)

        return Response({'updated': updated})
