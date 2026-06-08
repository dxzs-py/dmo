"""深度思考模式统一适配：更新 Ollama/Anthropic 的 special_params 和 capabilities

- Ollama: 添加 thinking special_params（reasoning=True），更新 qwen3/qwen3.5 的 capabilities
- Anthropic: 添加 thinking special_params，添加 deep_thinking capability
"""
from django.db import migrations


def update_thinking_config(apps, schema_editor):
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    AIModel = apps.get_model("ai_engine", "AIModel")

    # --- Ollama: 添加 thinking special_params ---
    try:
        ollama = AIProvider.objects.get(provider_id="ollama")
        ollama.special_params = {
            "thinking": {
                "type": "toggle",
                "label": "思考模式",
                "default": False,
                "model_kwarg": "reasoning",
                "pass_mode": "top_level",
                "enabled_value": True,
                "disabled_value": False,
            }
        }
        ollama.save()
    except AIProvider.DoesNotExist:
        pass

    # --- Anthropic: 添加 thinking special_params + deep_thinking capability ---
    try:
        anthropic = AIProvider.objects.get(provider_id="anthropic")
        anthropic.special_params = {
            "thinking": {
                "type": "toggle",
                "label": "扩展思考",
                "default": False,
                "model_kwarg": "thinking",
                "pass_mode": "top_level",
                "enabled_value": {"type": "enabled", "budget_tokens": 10000},
                "disabled_value": {"type": "disabled"},
            }
        }
        anthropic.save()

        # 为 Anthropic 模型添加 deep_thinking capability
        for model in AIModel.objects.filter(provider=anthropic):
            caps = model.capabilities or []
            if "deep_thinking" not in caps:
                caps.append("deep_thinking")
                model.capabilities = caps
                model.save()
    except AIProvider.DoesNotExist:
        pass

    # --- Ollama: 确认 qwen3/qwen3.5 有 deep_thinking capability ---
    try:
        ollama = AIProvider.objects.get(provider_id="ollama")
        for model in AIModel.objects.filter(provider=ollama):
            caps = model.capabilities or []
            model_name = model.name.lower()
            # qwen3 和 qwen3.5 系列支持思考模式
            if "qwen3" in model_name or "deepseek-r1" in model_name:
                if "deep_thinking" not in caps:
                    caps.append("deep_thinking")
                    model.capabilities = caps
                    model.save()
            # hermes3 不支持思考模式，移除
            if "hermes" in model_name and "deep_thinking" in caps:
                caps.remove("deep_thinking")
                model.capabilities = caps
                model.save()
    except AIProvider.DoesNotExist:
        pass


def reverse_thinking_config(apps, schema_editor):
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    AIModel = apps.get_model("ai_engine", "AIModel")

    # 恢复 Ollama special_params 为空
    try:
        ollama = AIProvider.objects.get(provider_id="ollama")
        ollama.special_params = {}
        ollama.save()
    except AIProvider.DoesNotExist:
        pass

    # 恢复 Anthropic special_params 和移除 deep_thinking
    try:
        anthropic = AIProvider.objects.get(provider_id="anthropic")
        anthropic.special_params = {}
        anthropic.save()

        for model in AIModel.objects.filter(provider=anthropic):
            caps = model.capabilities or []
            if "deep_thinking" in caps:
                caps.remove("deep_thinking")
                model.capabilities = caps
                model.save()
    except AIProvider.DoesNotExist:
        pass

    # 移除 Ollama 模型的 deep_thinking
    try:
        ollama = AIProvider.objects.get(provider_id="ollama")
        for model in AIModel.objects.filter(provider=ollama):
            caps = model.capabilities or []
            if "deep_thinking" in caps:
                caps.remove("deep_thinking")
                model.capabilities = caps
                model.save()
    except AIProvider.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0004_embedding_config_link_provider"),
    ]

    operations = [
        migrations.RunPython(update_thinking_config, reverse_thinking_config),
    ]
