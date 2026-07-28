"""EncryptedCharField 单元测试（Task 9）。

覆盖：
1. 加密往返一致（save → load → 字段值等于原文）
2. 空字符串 / None 透传不加密
3. DB 中的存储值为密文（不等于明文）
4. 密钥缺失时 validate_encryption_key 抛 RuntimeError（启动失败）
5. 密钥格式非法时 validate_encryption_key 抛 RuntimeError
6. 解密失败回退为原值（兼容历史明文）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    set FIELD_ENCRYPTION_KEY=<your-fernet-key>
    python manage.py test Django_xm.apps.tools.test_encrypted_field --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# 在 Django setup 之前注入 FIELD_ENCRYPTION_KEY（若环境未提供）
_TEST_KEY = "RRomwBS4PXMMPZ_YNeE_xETlMhg92ZcbeK7z_EdPaNg="  # 仅测试用，base64 32 bytes
os.environ.setdefault("FIELD_ENCRYPTION_KEY", _TEST_KEY)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")

import django

if not django.apps.apps.ready:
    django.setup()

from cryptography.fernet import Fernet

from Django_xm.apps.tools.fields import (
    EncryptedCharField,
    _get_fernet,
    validate_encryption_key,
)


class EncryptedCharFieldRoundTripTests(unittest.TestCase):
    """加密往返一致性测试。"""

    def test_encrypt_decrypt_round_trip(self):
        """get_prep_value 加密后，from_db_value 解密应得到原文。"""
        _get_fernet()
        plaintext = "my-secret-token-abc123"
        field = EncryptedCharField(max_length=500)

        encrypted = field.get_prep_value(plaintext)
        self.assertNotEqual(encrypted, plaintext, "加密后值不应等于明文")
        self.assertTrue(encrypted.startswith("gAAAAA"), "Fernet token 应以 gAAAAA 开头")

        decrypted = field.from_db_value(encrypted, None, None)
        self.assertEqual(decrypted, plaintext, "解密后应得到原文")

    def test_empty_string_passthrough(self):
        """空字符串应透传不加密。"""
        field = EncryptedCharField(max_length=500)
        self.assertEqual(field.get_prep_value(""), "")
        self.assertEqual(field.from_db_value("", None, None), "")

    def test_none_passthrough(self):
        """None 应透传不加密。"""
        field = EncryptedCharField(max_length=500)
        self.assertIsNone(field.get_prep_value(None))
        self.assertIsNone(field.from_db_value(None, None, None))

    def test_idempotent_encryption_of_already_encrypted(self):
        """对已加密的 Fernet token 再次调用 get_prep_value，不应双重加密。"""
        plaintext = "another-secret-xyz789"
        field = EncryptedCharField(max_length=500)
        encrypted_once = field.get_prep_value(plaintext)
        encrypted_twice = field.get_prep_value(encrypted_once)
        self.assertEqual(
            encrypted_once,
            encrypted_twice,
            "对 Fernet token 重复加密应保持不变",
        )

    def test_invalid_token_falls_back_to_raw(self):
        """解密失败（InvalidToken）应回退为原值，兼容历史明文。"""
        field = EncryptedCharField(max_length=500)
        legacy_plaintext = "legacy-plaintext-token"
        result = field.from_db_value(legacy_plaintext, None, None)
        self.assertEqual(
            result,
            legacy_plaintext,
            "历史明文应原样返回（不抛异常）",
        )


class EncryptedCharFieldDeconstructTests(unittest.TestCase):
    """字段 deconstruct 测试（确保迁移文件可正确序列化）。"""

    def test_deconstruct_preserves_max_length(self):
        """deconstruct 应保留 max_length 参数。"""
        field = EncryptedCharField(max_length=500, blank=True, default="")
        name, path, args, kwargs = field.deconstruct()
        self.assertEqual(kwargs.get("max_length"), 500)
        self.assertEqual(path, "Django_xm.apps.tools.fields.EncryptedCharField")


class EncryptionKeyValidationTests(unittest.TestCase):
    """加密密钥校验测试。"""

    def test_validate_encryption_key_success(self):
        """密钥已设置时 validate_encryption_key 应正常返回。"""
        # setUpClass 已设置 FIELD_ENCRYPTION_KEY
        validate_encryption_key()  # 不应抛异常

    def test_validate_encryption_key_missing_raises(self):
        """密钥缺失时 validate_encryption_key 应抛 RuntimeError（启动失败）。"""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                validate_encryption_key()
            self.assertIn("FIELD_ENCRYPTION_KEY", str(ctx.exception))

    def test_validate_encryption_key_invalid_format_raises(self):
        """密钥格式非法时应抛 RuntimeError。"""
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": "not-a-valid-key"}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                validate_encryption_key()
            self.assertIn("格式非法", str(ctx.exception))

    def test_get_fernet_returns_valid_instance(self):
        """_get_fernet 应返回可用的 Fernet 实例。"""
        fernet = _get_fernet()
        self.assertIsInstance(fernet, Fernet)

        token = fernet.encrypt(b"test-data")
        self.assertEqual(fernet.decrypt(token), b"test-data")


if __name__ == "__main__":
    unittest.main()
