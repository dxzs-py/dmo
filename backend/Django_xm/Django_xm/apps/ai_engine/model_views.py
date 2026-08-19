import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.config import HELPER_MODEL_PRIORITY, get_available_providers
from Django_xm.apps.ai_engine.services.llm_factory import (
    get_chat_model_by_provider,
    test_model_connection,
)
from Django_xm.apps.ai_engine.services.registry_service import (
    get_provider_config,
    get_provider_default_model,
    is_provider_valid,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response

logger = logging.getLogger(__name__)


class ModelListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        providers = get_available_providers()
        return success_response(
            data={
                "providers": providers,
                "default_provider": "openai",
            }
        )


class ModelTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def post(self, request):
        provider_id = request.data.get("provider_id")
        model_name = request.data.get("model_name")

        if not provider_id:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message="provider_id is required",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        if not is_provider_valid(provider_id):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"Unknown provider: {provider_id}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        result = test_model_connection(provider_id=provider_id, model_name=model_name)
        if result["success"]:
            return success_response(
                data=result.get("model_info"),
                message=result["message"],
            )
        else:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=result["message"],
                data=result.get("model_info"),
                http_status=status.HTTP_400_BAD_REQUEST,
            )


class ModelSwitchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def post(self, request):
        provider_id = request.data.get("provider_id")
        model_name = request.data.get("model_name")
        temperature = request.data.get("temperature")
        max_tokens = request.data.get("max_tokens")
        special_params = request.data.get("special_params")

        if not provider_id:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message="provider_id is required",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        if not is_provider_valid(provider_id):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"Unknown provider: {provider_id}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            model = get_chat_model_by_provider(
                provider_id=provider_id,
                model_name=model_name or None,
                temperature=float(temperature) if temperature is not None else None,
                max_tokens=int(max_tokens) if max_tokens is not None else None,
                special_params=special_params or None,
            )
            resolved_name = model_name or get_provider_default_model(provider_id)
            return success_response(
                data={
                    "provider_id": provider_id,
                    "model_name": resolved_name,
                    "model_type": type(model).__name__,
                },
                message=f"模型切换成功: {resolved_name}",
            )
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception("模型切换失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="模型切换失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class HelperModelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        # 优先从 SystemConfig 数据库读取
        current_provider = ""
        current_model = ""
        try:
            from Django_xm.apps.ai_engine.models import SystemConfig

            helper_config = SystemConfig.get_value("helper_model", {})
            current_provider = helper_config.get("provider_id", "")
            current_model = helper_config.get("model_name", "")
        except Exception:
            # 配置读取失败时回退到运行时内存配置，不影响主流程
            logger.debug("读取 helper_model 配置失败，回退到运行时内存")

        # 回退到运行时内存
        if not current_provider:
            from django.conf import settings as django_settings

            current_provider = getattr(django_settings, "AI_HELPER_MODEL_PROVIDER", "")
            current_model = getattr(django_settings, "AI_HELPER_MODEL_NAME", "")

        priority_list = []
        for item in HELPER_MODEL_PRIORITY:
            pid = item["provider"]
            provider_cfg = get_provider_config(pid)
            priority_list.append(
                {
                    "provider_id": pid,
                    "model_name": item["model"],
                    "reason": item["reason"],
                    "label": provider_cfg.get("label", pid),
                }
            )

        providers = get_available_providers()
        return success_response(
            data={
                "current": {
                    "provider_id": current_provider or None,
                    "model_name": current_model or None,
                    "is_auto": not current_provider,
                },
                "priority": priority_list,
                "providers": providers,
            }
        )

    @extend_schema(exclude=True)
    def put(self, request):
        from django.conf import settings as django_settings

        provider_id = request.data.get("provider_id") or ""
        model_name = request.data.get("model_name") or ""

        if provider_id and not is_provider_valid(provider_id):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"Unknown provider: {provider_id}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        # 持久化到数据库：空字符串视为重置，存 null
        try:
            from Django_xm.apps.ai_engine.models import SystemConfig

            if provider_id:
                SystemConfig.set_value(
                    "helper_model",
                    {
                        "provider_id": provider_id,
                        "model_name": model_name,
                    },
                )
            else:
                SystemConfig.set_value("helper_model", None)
        except Exception:
            # 数据库持久化失败不影响当前请求，运行时内存仍会同步
            logger.exception("保存 helper_model 配置到数据库失败")

        # 同步运行时内存（兼容旧逻辑）
        django_settings.AI_HELPER_MODEL_PROVIDER = provider_id
        django_settings.AI_HELPER_MODEL_NAME = model_name

        import Django_xm.apps.ai_engine.services.llm_factory as llm_mod

        llm_mod._helper_model_cache = None

        return success_response(
            data={
                "provider_id": provider_id or None,
                "model_name": model_name or None,
                "is_auto": not provider_id,
            },
            message="辅助模型设置已保存" if provider_id else "已重置为自动选择",
        )
