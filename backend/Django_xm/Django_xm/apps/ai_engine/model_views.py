from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.config import get_available_providers
from Django_xm.apps.ai_engine.services.llm_factory import (
    get_system_default_chat_model,
    test_model_connection,
)
from Django_xm.apps.ai_engine.services.registry_service import is_provider_valid
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response

# 未配置系统默认时的兜底 provider（与历史硬编码行为一致）
FALLBACK_DEFAULT_PROVIDER = "openai"


class ModelListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        providers = get_available_providers()
        # 系统默认聊天模型（SystemConfig.default_chat_model，公开非敏感信息）：
        # 普通用户加载模型列表时据此直接落到管理员设定的默认模型，
        # 无需再请求 IsAdminUser 的 /ai-engine/settings/（消除 403 噪音）。
        system_default = get_system_default_chat_model()
        return success_response(
            data={
                "providers": providers,
                "default_provider": (
                    system_default["provider_id"]
                    if system_default
                    else FALLBACK_DEFAULT_PROVIDER
                ),
                "default_model": (
                    system_default.get("model_name") if system_default else None
                ),
            }
        )


class ModelTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def post(self, request):
        provider_id = request.data.get("provider_id")
        model_name = request.data.get("model_name")

        if not provider_id:
            raise BaseAppError("provider_id is required", business_code=ErrorCode.INVALID_PARAMS)

        if not is_provider_valid(provider_id):
            raise BaseAppError(f"Unknown provider: {provider_id}", business_code=ErrorCode.INVALID_PARAMS)

        result = test_model_connection(provider_id=provider_id, model_name=model_name)
        if result["success"]:
            return success_response(
                data=result.get("model_info"),
                message=result["message"],
            )
        else:
            raise BaseAppError(
                result["message"],
                business_code=ErrorCode.INVALID_PARAMS,
                data=result.get("model_info"),
            )
