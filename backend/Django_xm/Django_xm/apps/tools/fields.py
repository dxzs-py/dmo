"""
工具 app 自定义字段

提供敏感数据加密存储能力：
- ``EncryptedCharField``：基于 ``cryptography.fernet`` 的对称加密 CharField

加密密钥来源于 ``FIELD_ENCRYPTION_KEY`` 环境变量（Fernet 兼容的 base64 编码 32 字节），
可通过以下命令生成::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

设计要点：
- DB 列类型为 TEXT，可容纳变长的 Fernet token（含 IV/HMAC，密文长度 > 明文）
- ``max_length`` 仅作用于明文（Python 端校验），不影响 DB 列定义
- 空字符串 / None 透传不加密
- ``from_db_value`` 解密失败时回退为原值，兼容历史明文迁移
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from django.db import models

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    """从 ``FIELD_ENCRYPTION_KEY`` 环境变量构造 Fernet 实例。

    Returns:
        Fernet 实例。

    Raises:
        RuntimeError: 当环境变量未设置或格式非法时。
    """
    key = os.environ.get("FIELD_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY 环境变量未设置。请生成 Fernet 密钥并配置：\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"FIELD_ENCRYPTION_KEY 格式非法（应为 base64 编码的 32 字节）: {exc}") from exc


def validate_encryption_key() -> None:
    """供 AppConfig.ready() 调用，启动时校验加密密钥可用。

    Raises:
        RuntimeError: 当密钥缺失或格式非法时，启动失败。
    """
    _get_fernet()


class EncryptedCharField(models.TextField):
    """加密字符串字段。

    - DB 存储 Fernet token（base64 文本）
    - Python 层返回明文
    - ``max_length`` 作用于明文（校验用），不影响 DB 列定义
    - 空字符串 / None 透传不加密

    Example:
        auth_token = EncryptedCharField(max_length=500, blank=True, default='')
    """

    description = "Encrypted string field (Fernet)"

    def __init__(self, *args, max_length: int | None = None, **kwargs):
        # TextField 不接受 max_length，需要单独保存以供表单校验/deconstruct 使用
        self.max_length = max_length
        super().__init__(*args, **kwargs)
        # 恢复 max_length 属性（TextField.__init__ 不会设置）
        if max_length is not None:
            self.max_length = max_length

    def deconstruct(self):
        """序列化为迁移文件中的字段定义。"""
        name, path, args, kwargs = super().deconstruct()
        if self.max_length is not None:
            kwargs["max_length"] = self.max_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        """DB → Python：解密 Fernet token；空值透传；解密失败回退原值。"""
        if value is None or value == "":
            return value
        try:
            fernet = _get_fernet()
        except RuntimeError:
            # 启动早期 / 管理命令（如 makemigrations）可能未配置密钥
            logger.warning("EncryptedCharField.from_db_value: FIELD_ENCRYPTION_KEY 未配置，返回原值")
            return value
        try:
            return fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            # 历史明文 / 密钥轮换后旧密文 → 返回原值由上层处理
            logger.warning("EncryptedCharField.from_db_value: 解密失败，返回原值（可能是历史明文）")
            return value

    def to_python(self, value):
        """反序列化（如表单清洗）→ Python 值。"""
        if value is None or value == "":
            return value
        if isinstance(value, str):
            try:
                fernet = _get_fernet()
            except RuntimeError:
                return value
            try:
                return fernet.decrypt(value.encode()).decode()
            except InvalidToken:
                return value
        return value

    def get_prep_value(self, value):
        """Python → DB：明文加密为 Fernet token；空值透传。"""
        if value is None or value == "":
            return value
        if not isinstance(value, str):
            value = str(value)
        # 跳过已加密的 Fernet token，避免重复加密
        try:
            fernet = _get_fernet()
        except RuntimeError:
            logger.warning("EncryptedCharField.get_prep_value: FIELD_ENCRYPTION_KEY 未配置，明文落库")
            return value
        try:
            fernet.decrypt(value.encode())
            return value  # 已是合法 Fernet token
        except InvalidToken:
            pass
        return fernet.encrypt(value.encode()).decode()

    def value_to_string(self, obj):
        """序列化器使用：返回 DB 可存储的密文。"""
        value = self.value_from_object(obj)
        return self.get_prep_value(value)
