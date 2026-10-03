from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.config import get_available_providers
from Django_xm.apps.ai_engine.services.llm_factory import test_model_connection
from Django_xm.apps.ai_engine.services.registry_service import is_provider_valid
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response


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
