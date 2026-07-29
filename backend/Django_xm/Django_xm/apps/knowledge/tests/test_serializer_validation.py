"""知识库 Serializer 校验完整性测试（Task 13.1 + 13.2）。

覆盖 spec `fix-backend-audit-findings` SubTask 13.1 / 13.2：
- IndexCreateSerializer / EmptyIndexCreateSerializer 共享 validate_name 正则 + max_length=100
- RagQuerySerializer.k / SearchRequestSerializer.k max_value=20

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m pytest \
        Django_xm/apps/knowledge/tests/test_serializer_validation.py -v --tb=short
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()


from Django_xm.apps.knowledge.serializers import (
    EmptyIndexCreateSerializer,
    IndexCreateSerializer,
    RagQuerySerializer,
    SearchRequestSerializer,
)

# ============================================================================
# SubTask 13.1: IndexCreateSerializer / EmptyIndexCreateSerializer
# ============================================================================


class IndexCreateSerializerNameValidationTest(unittest.TestCase):
    """IndexCreateSerializer.name 校验测试。"""

    def test_valid_name_with_chinese_passes(self) -> None:
        """合法名称（含中文）通过校验。"""
        s = IndexCreateSerializer(data={"name": "知识库_测试-001"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_valid_name_ascii_passes(self) -> None:
        """纯 ASCII 名称通过校验。"""
        s = IndexCreateSerializer(data={"name": "my_index-001"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_too_long_name_rejected(self) -> None:
        """超长 name（>100）被拒绝。"""
        long_name = "a" * 101
        s = IndexCreateSerializer(data={"name": long_name})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_at_max_length_passes(self) -> None:
        """正好 100 字符的 name 通过校验。"""
        name = "a" * 100
        s = IndexCreateSerializer(data={"name": name})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_name_with_space_rejected(self) -> None:
        """包含空格的 name 被拒绝。"""
        s = IndexCreateSerializer(data={"name": "name with space"})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_with_dot_rejected(self) -> None:
        """包含点号的 name 被拒绝。"""
        s = IndexCreateSerializer(data={"name": "name.with.dot"})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_with_slash_rejected(self) -> None:
        """包含斜杠的 name 被拒绝。"""
        s = IndexCreateSerializer(data={"name": "name/with/slash"})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_empty_name_rejected(self) -> None:
        """空 name 被拒绝。"""
        s = IndexCreateSerializer(data={"name": ""})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_missing_name_rejected(self) -> None:
        """缺少 name 字段被拒绝。"""
        s = IndexCreateSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)


class EmptyIndexCreateSerializerNameValidationTest(unittest.TestCase):
    """EmptyIndexCreateSerializer.name 校验测试。"""

    def test_valid_name_with_chinese_passes(self) -> None:
        """合法名称（含中文）通过校验。"""
        s = EmptyIndexCreateSerializer(data={"name": "空索引_测试-001"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_too_long_name_rejected(self) -> None:
        """超长 name（>100）被拒绝。"""
        long_name = "a" * 101
        s = EmptyIndexCreateSerializer(data={"name": long_name})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_name_with_space_rejected(self) -> None:
        """包含空格的 name 被拒绝。"""
        s = EmptyIndexCreateSerializer(data={"name": "name with space"})
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)

    def test_shared_validation_logic(self) -> None:
        """两个 serializer 共享同一正则规则。"""
        invalid_name = "invalid.name"
        s1 = IndexCreateSerializer(data={"name": invalid_name})
        s2 = EmptyIndexCreateSerializer(data={"name": invalid_name})
        self.assertFalse(s1.is_valid())
        self.assertFalse(s2.is_valid())
        self.assertIn("name", s1.errors)
        self.assertIn("name", s2.errors)


# ============================================================================
# SubTask 13.2: RagQuerySerializer.k / SearchRequestSerializer.k max_value=20
# ============================================================================


class RagQuerySerializerKValidationTest(unittest.TestCase):
    """RagQuerySerializer.k 字段 max_value=20 测试。"""

    def test_k_default_is_four(self) -> None:
        """k 缺省时默认 4。"""
        s = RagQuerySerializer(data={"index_name": "idx", "query": "q"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["k"], 4)

    def test_k_at_max_passes(self) -> None:
        """k = 20 通过校验。"""
        s = RagQuerySerializer(data={"index_name": "idx", "query": "q", "k": 20})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["k"], 20)

    def test_k_above_max_rejected(self) -> None:
        """k = 21 被拒绝。"""
        s = RagQuerySerializer(data={"index_name": "idx", "query": "q", "k": 21})
        self.assertFalse(s.is_valid())
        self.assertIn("k", s.errors)


class SearchRequestSerializerKValidationTest(unittest.TestCase):
    """SearchRequestSerializer.k 字段 max_value=20 测试。"""

    def test_k_default_is_four(self) -> None:
        """k 缺省时默认 4。"""
        s = SearchRequestSerializer(data={"index_name": "idx", "query": "q"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["k"], 4)

    def test_k_at_max_passes(self) -> None:
        """k = 20 通过校验。"""
        s = SearchRequestSerializer(data={"index_name": "idx", "query": "q", "k": 20})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["k"], 20)

    def test_k_above_max_rejected(self) -> None:
        """k = 100 被拒绝。"""
        s = SearchRequestSerializer(data={"index_name": "idx", "query": "q", "k": 100})
        self.assertFalse(s.is_valid())
        self.assertIn("k", s.errors)


if __name__ == "__main__":
    unittest.main()
