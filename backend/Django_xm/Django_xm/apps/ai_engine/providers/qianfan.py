from typing import Any


# 延迟导入，避免循环依赖
def _get_registry_config() -> dict[str, Any]:
    from Django_xm.apps.ai_engine.services.registry_service import get_provider_config
    return get_provider_config("baidu_qianfan")


def get_provider_config() -> dict[str, Any]:
    return _get_registry_config().copy()
