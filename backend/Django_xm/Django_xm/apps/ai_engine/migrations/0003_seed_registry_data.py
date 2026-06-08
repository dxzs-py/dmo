"""种子数据：从 config.py MODEL_REGISTRY 和 embedding_factory.py EMBEDDING_PROVIDER_REGISTRY 导入"""

from django.db import migrations


def seed_registry_data(apps, schema_editor):
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    AIModel = apps.get_model("ai_engine", "AIModel")
    EmbeddingProviderConfig = apps.get_model("ai_engine", "EmbeddingProviderConfig")

    # ==================== LLM Provider + Model 种子 ====================
    providers_data = [
        {
            "provider_id": "openai",
            "provider": "openai",
            "label": "OpenAI",
            "icon": "\U0001f535",
            "default_model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "api_key_attr": "openai_api_key",
            "base_url_attr": "openai_api_base",
            "model_attr": "openai_model",
            "special_params": {},
            "presets": {
                "default": {
                    "model_name": "gpt-4o",
                    "model_provider": "openai",
                    "temperature": 0.7,
                    "description": "默认模型，平衡性能和成本",
                },
                "fast": {
                    "model_name": "gpt-4o-mini",
                    "model_provider": "openai",
                    "temperature": 0.7,
                    "description": "快速模型，适合简单任务",
                },
                "precise": {
                    "model_name": "gpt-4o",
                    "model_provider": "openai",
                    "temperature": 0.3,
                    "description": "精确模型，适合需要准确性的任务",
                },
                "creative": {
                    "model_name": "gpt-4o",
                    "model_provider": "openai",
                    "temperature": 1.0,
                    "description": "创意模型，适合需要创造性的任务",
                },
            },
            "is_enabled": True,
            "sort_order": 0,
            "models": [
                {"name": "gpt-4o", "capabilities": ["tool_calling", "streaming"]},
                {"name": "gpt-4o-mini", "capabilities": ["tool_calling", "streaming"]},
                {"name": "gpt-4-turbo", "capabilities": ["tool_calling", "streaming"]},
            ],
        },
        {
            "provider_id": "deepseek",
            "provider": "deepseek",
            "label": "DeepSeek",
            "icon": "\U0001f916",
            "default_model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
            "api_key_attr": "deepseek_api_key",
            "base_url_attr": "deepseek_api_base",
            "model_attr": "deepseek_model",
            "special_params": {
                "thinking": {
                    "type": "toggle",
                    "label": "思考模式",
                    "description": "启用 DeepSeek 思考模式（深度思考模式下自动启用，其他模式下默认关闭）",
                    "default": False,
                    "model_kwarg": "thinking",
                    "pass_mode": "extra_body",
                    "enabled_value": {"type": "enabled"},
                    "disabled_value": {"type": "disabled"},
                },
                "reasoning_effort": {
                    "type": "select",
                    "label": "思考强度",
                    "description": "推理努力程度（high: 适合大多数场景, max: 适合复杂推理任务）",
                    "options": ["high", "max"],
                    "default": "high",
                    "model_kwarg": "reasoning_effort",
                    "pass_mode": "top_level",
                },
            },
            "presets": {},
            "is_enabled": True,
            "sort_order": 1,
            "models": [
                {"name": "deepseek-v4-flash", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
                {"name": "deepseek-v4-pro", "capabilities": ["tool_calling", "deep_thinking", "streaming"]},
            ],
        },
        {
            "provider_id": "groq",
            "provider": "groq",
            "label": "Groq",
            "icon": "\u26a1",
            "default_model": "llama-3.3-70b-versatile",
            "api_key_env": "GROQ_API_KEY",
            "api_key_attr": "groq_api_key",
            "base_url_attr": None,
            "model_attr": "groq_model",
            "special_params": {},
            "presets": {},
            "is_enabled": True,
            "sort_order": 2,
            "models": [
                {"name": "llama-3.3-70b-versatile", "capabilities": ["tool_calling", "streaming"]},
            ],
        },
        {
            "provider_id": "baidu_qianfan",
            "provider": "openai",
            "label": "\u767e\u5ea6\u5343\u5e06",
            "icon": "\U0001f7e0",
            "default_model": "ernie-3.5-8k",
            "api_key_env": "BAIDU_QIANFAN_API_KEY",
            "api_key_attr": "baidu_qianfan_api_key",
            "base_url_attr": "baidu_qianfan_api_base",
            "model_attr": "baidu_qianfan_model",
            "special_params": {},
            "presets": {},
            "is_enabled": True,
            "sort_order": 3,
            "models": [
                {"name": "ernie-3.5-8k", "capabilities": ["tool_calling", "streaming"]},
            ],
        },
        {
            "provider_id": "anthropic",
            "provider": "anthropic",
            "label": "Anthropic",
            "icon": "\U0001f7e3",
            "default_model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
            "api_key_attr": "anthropic_api_key",
            "base_url_attr": None,
            "model_attr": None,
            "special_params": {},
            "presets": {
                "anthropic_default": {
                    "model_name": "claude-sonnet-4-20250514",
                    "model_provider": "anthropic",
                    "temperature": 0.7,
                    "description": "Anthropic Claude Sonnet 4，平衡性能和成本",
                },
                "anthropic_fast": {
                    "model_name": "claude-3-5-haiku-20241022",
                    "model_provider": "anthropic",
                    "temperature": 0.7,
                    "description": "Anthropic Claude Haiku，快速响应",
                },
                "anthropic_precise": {
                    "model_name": "claude-sonnet-4-20250514",
                    "model_provider": "anthropic",
                    "temperature": 0.3,
                    "description": "Anthropic Claude Sonnet 4，精确模式",
                },
            },
            "is_enabled": True,
            "sort_order": 4,
            "models": [
                {"name": "claude-sonnet-4-20250514", "capabilities": ["tool_calling", "streaming"]},
                {"name": "claude-3-5-haiku-20241022", "capabilities": ["tool_calling", "streaming"]},
            ],
        },
        {
            "provider_id": "ollama",
            "provider": "ollama",
            "label": "Ollama \u672c\u5730",
            "icon": "\U0001f999",
            "default_model": "qwen3:8b",
            "api_key_env": None,
            "api_key_attr": None,
            "base_url_attr": "ollama_base_url",
            "model_attr": "ollama_model",
            "special_params": {},
            "presets": {
                "ollama_default": {
                    "model_name": "qwen3:8b",
                    "model_provider": "ollama",
                    "temperature": 0.7,
                    "description": "Ollama 本地 qwen3 8B，无需 API key",
                },
                "ollama_qwen35": {
                    "model_name": "qwen3.5:9b",
                    "model_provider": "ollama",
                    "temperature": 0.7,
                    "description": "Ollama 本地 qwen3.5 9B",
                },
                "ollama_hermes": {
                    "model_name": "hermes3:8b",
                    "model_provider": "ollama",
                    "temperature": 0.7,
                    "description": "Ollama 本地 hermes3 8B",
                },
            },
            "is_enabled": True,
            "sort_order": 5,
            "models": [
                {"name": "qwen3:8b", "capabilities": ["tool_calling", "streaming", "deep_thinking"]},
                {"name": "qwen3.5:9b", "capabilities": ["tool_calling", "streaming"]},
                {"name": "hermes3:8b", "capabilities": ["tool_calling", "streaming"]},
            ],
        },
    ]

    for idx, pdata in enumerate(providers_data):
        models_list = pdata.pop("models")
        provider_obj, _ = AIProvider.objects.update_or_create(
            provider_id=pdata["provider_id"],
            defaults=pdata,
        )
        for m_idx, mdata in enumerate(models_list):
            AIModel.objects.update_or_create(
                provider=provider_obj,
                name=mdata["name"],
                defaults={
                    "capabilities": mdata.get("capabilities", []),
                    "sort_order": m_idx,
                },
            )

    # ==================== Embedding Provider 种子 ====================
    embedding_data = [
        {
            "provider_id": "openai",
            "label": "OpenAI(text-embedding-3-small)",
            "factory_path": "Django_xm.apps.ai_engine.providers.openai_embedding.create_embedding",
            "key_attr": "openai_api_key",
            "dimension": 1536,
            "supported_params": ["batch_size", "model"],
            "is_enabled": True,
            "sort_order": 0,
        },
        {
            "provider_id": "ollama",
            "label": "Ollama 本地(bge-m3)",
            "factory_path": "Django_xm.apps.ai_engine.providers.ollama_embedding.create_embedding",
            "key_attr": None,
            "dimension": 1024,
            "supported_params": ["model"],
            "is_enabled": True,
            "sort_order": 1,
        },
        {
            "provider_id": "baidu_qianfan",
            "label": "\u767e\u5ea6\u5343\u5e06(QianfanEmbeddingsEndpoint)",
            "factory_path": "Django_xm.apps.ai_engine.providers.qianfan_embedding.create_embedding",
            "key_attr": "baidu_qianfan_api_key",
            "dimension": 1536,
            "supported_params": ["model"],
            "is_enabled": True,
            "sort_order": 2,
        },
        {
            "provider_id": "local",
            "label": "\u672c\u5730HuggingFace(BAAI/bge-small-zh-v1.5)",
            "factory_path": "Django_xm.apps.ai_engine.providers.local_embedding.create_embedding",
            "key_attr": None,
            "dimension": 512,
            "supported_params": ["model_name"],
            "is_enabled": False,
            "sort_order": 3,
        },
    ]

    for edata in embedding_data:
        EmbeddingProviderConfig.objects.update_or_create(
            provider_id=edata["provider_id"],
            defaults=edata,
        )


def rollback_registry_data(apps, schema_editor):
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    AIModel = apps.get_model("ai_engine", "AIModel")
    EmbeddingProviderConfig = apps.get_model("ai_engine", "EmbeddingProviderConfig")

    seed_provider_ids = ["openai", "deepseek", "groq", "baidu_qianfan", "anthropic", "ollama"]
    seed_embedding_ids = ["openai", "ollama", "baidu_qianfan", "local"]

    AIModel.objects.filter(provider__provider_id__in=seed_provider_ids).delete()
    AIProvider.objects.filter(provider_id__in=seed_provider_ids).delete()
    EmbeddingProviderConfig.objects.filter(provider_id__in=seed_embedding_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0002_aiprovider_embeddingproviderconfig_aimodel"),
    ]

    operations = [
        migrations.RunPython(seed_registry_data, rollback_registry_data),
    ]
