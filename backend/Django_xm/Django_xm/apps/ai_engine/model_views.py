from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.ai_engine.config import get_available_providers, MODEL_REGISTRY
from Django_xm.apps.ai_engine.services.llm_factory import (
    get_chat_model_by_provider,
    test_model_connection,
)


class ModelListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        providers = get_available_providers()
        return Response({
            "code": 200,
            "message": "success",
            "data": {
                "providers": providers,
                "default_provider": "openai",
            },
        })


class ModelTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        provider_id = request.data.get("provider_id")
        model_name = request.data.get("model_name")

        if not provider_id:
            return Response(
                {"code": 400, "message": "provider_id is required", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if provider_id not in MODEL_REGISTRY:
            return Response(
                {"code": 400, "message": f"Unknown provider: {provider_id}", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = test_model_connection(provider_id=provider_id, model_name=model_name)
        code = 200 if result["success"] else 400
        return Response(
            {"code": code, "message": result["message"], "data": result.get("model_info")},
            status=status.HTTP_200_OK if result["success"] else status.HTTP_400_BAD_REQUEST,
        )


class ModelSwitchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        provider_id = request.data.get("provider_id")
        model_name = request.data.get("model_name")
        temperature = request.data.get("temperature")
        max_tokens = request.data.get("max_tokens")
        special_params = request.data.get("special_params")

        if not provider_id:
            return Response(
                {"code": 400, "message": "provider_id is required", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if provider_id not in MODEL_REGISTRY:
            return Response(
                {"code": 400, "message": f"Unknown provider: {provider_id}", "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            model = get_chat_model_by_provider(
                provider_id=provider_id,
                model_name=model_name or None,
                temperature=float(temperature) if temperature is not None else None,
                max_tokens=int(max_tokens) if max_tokens is not None else None,
                special_params=special_params or None,
            )
            resolved_name = model_name or MODEL_REGISTRY[provider_id]["default_model"]
            return Response({
                "code": 200,
                "message": f"模型切换成功: {resolved_name}",
                "data": {
                    "provider_id": provider_id,
                    "model_name": resolved_name,
                    "model_type": type(model).__name__,
                },
            })
        except ValueError as e:
            return Response(
                {"code": 400, "message": str(e), "data": None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"code": 500, "message": f"模型切换失败: {str(e)}", "data": None},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
