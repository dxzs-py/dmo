import logging

from .builtin import BUILTIN_CAPABILITIES
from .registry import registry

logger = logging.getLogger(__name__)


def setup_default_capabilities() -> None:
    for capability in BUILTIN_CAPABILITIES:
        existing = registry.get(capability.name)
        if existing is not None:
            logger.debug("Capability '%s' already registered, skipping", capability.name)
            continue
        registry.register(capability.name, capability)
    logger.info(
        "Default capabilities registered: %s",
        registry.list_capabilities(),
    )
