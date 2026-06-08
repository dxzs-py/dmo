from typing import Any, Dict


# 延迟导入，避免循环依赖
def _get_registry_config() -> Dict[str, Any]:
    from Django_xm.apps.ai_engine.services.registry_service import get_provider_config
    return get_provider_config("anthropic")


def get_provider_config() -> Dict[str, Any]:
    return _get_registry_config().copy()
