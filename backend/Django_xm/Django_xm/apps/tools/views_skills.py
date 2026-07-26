import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.common.responses import success_response, error_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.apps.tools.managers import _build_user_tool_category_info
from Django_xm.apps.tools.views_custom import StandardPagination, _handle_crud_result

logger = logging.getLogger(__name__)


class SkillListView(APIView):
    """Skill 列表视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager

        manager = SkillToolManager()
        try:
            skills = manager.get_tool_info_list(request.user)
            return success_response(data={'skills': skills, 'total': len(skills)})
        except Exception as e:
            logger.error(f"获取 Skill 列表失败: {e}")
            return error_response(message=f"获取 Skill 列表失败: {str(e)}")


class SkillCreateView(APIView):
    """创建自定义 Skill 视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.serializers import SkillCreateSerializer
        from Django_xm.apps.tools.managers import SkillToolManager

        serializer = SkillCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.create_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get('message', 'Skill 创建成功'))


class SkillDeleteView(APIView):
    """删除自定义 Skill 视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.serializers import SkillDeleteSerializer
        from Django_xm.apps.tools.managers import SkillToolManager

        serializer = SkillDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data['name']
        manager = SkillToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get('message', 'Skill 已删除'))


class SkillUpdateView(APIView):
    """更新自定义 Skill 视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.serializers import SkillCreateSerializer
        from Django_xm.apps.tools.managers import SkillToolManager

        serializer = SkillCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.update_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get('message', 'Skill 更新成功'))


class SkillToggleView(APIView):
    """切换自定义 Skill 状态视图"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.serializers import SkillToggleSerializer
        from Django_xm.apps.tools.managers import SkillToolManager

        serializer = SkillToggleSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data['name']
        status = serializer.validated_data.get('status')
        manager = SkillToolManager()
        result = manager.toggle_user_tool(name, request.user, status)
        return _handle_crud_result(
            result, result.get('message', '状态切换成功'),
            data={'name': name, 'status': result.get('status')},
        )


# ============================================================
# Skill 包管理（Agent Skills 规范）
# ============================================================

class SkillPackageListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.models import SkillPackage

        user_skills = SkillPackage.objects.filter(user=request.user).select_related('category')
        system_skills = SkillPackage.objects.filter(source='system').select_related('category')
        all_skills = user_skills | system_skills

        data = []
        for pkg in all_skills:
            data.append({
                'name': pkg.name,
                'description': pkg.description,
                'version': pkg.version,
                'license': pkg.license,
                'compatibility': pkg.compatibility,
                'tool_type': pkg.tool_type,
                'category': _build_user_tool_category_info(pkg) if pkg.category else {"code": "general", "name": "通用"},
                'source': pkg.source,
                'status': pkg.status,
                'visibility': 'selectable',
                'tier': 'extended',
                'created_at': pkg.created_at.isoformat() if pkg.created_at else None,
                'updated_at': pkg.updated_at.isoformat() if pkg.updated_at else None,
            })
        paginator = StandardPagination()
        page = paginator.paginate_queryset(data, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data=data)


class SkillPackageUploadView(APIView):
    """上传 Skill 包（ZIP 压缩包）"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return error_response(message="请上传 ZIP 文件")

        if not uploaded_file.name.endswith('.zip'):
            return error_response(message="仅支持 .zip 格式的 Skill 包")

        # 保存临时文件
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            for chunk in uploaded_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            loader = SkillLoader()
            success, message, metadata = loader.install_skill_package(tmp_path, request.user, source='user')
            if success:
                return success_response(data=metadata, message=message)
            return error_response(message=message)
        finally:
            import os
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class SkillPackageDeleteView(APIView):
    """删除 Skill 包"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        name = request.data.get('name')
        if not name:
            return error_response(message="请提供 name 参数")

        loader = SkillLoader()
        success, message = loader.uninstall_skill_package(name, request.user)
        if success:
            return success_response(message=message)
        http_status = 403 if '系统级' in message else 400
        return error_response(message=message, http_status=http_status)


class SkillPackageToggleView(APIView):
    """启用/禁用 Skill 包"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.models import SkillPackage

        name = request.data.get('name')
        if not name:
            return error_response(message="请提供 name 参数")

        status = request.data.get('status')
        pkg = SkillPackage.objects.filter(user=request.user, name=name).first()
        if not pkg:
            return error_response(message=f"未找到 Skill 包 '{name}'")

        if status in ('active', 'disabled'):
            pkg.status = status
        else:
            pkg.status = 'disabled' if pkg.status == 'active' else 'active'

        pkg.save()
        return success_response(data={'name': name, 'status': pkg.status}, message=f"Skill '{name}' 状态已更新为 {pkg.status}")


class SkillPackageDetailView(APIView):
    """查看 Skill 包 SKILL.md 内容"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        name = request.query_params.get('name')
        if not name:
            return error_response(message="请提供 name 参数")

        loader = SkillLoader()
        content = loader.activate_skill(name, user_id=request.user.id)
        if content is None:
            return error_response(message=f"未找到 Skill '{name}' 或 SKILL.md 不存在")

        return success_response(data={
            'name': name,
            'instructions': content,
        })
