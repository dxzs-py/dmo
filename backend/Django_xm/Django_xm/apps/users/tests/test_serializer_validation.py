"""用户注册 Serializer 校验完整性测试（Task 13.5）。

覆盖 spec `fix-backend-audit-findings` SubTask 13.5：
- UserRegisterSerializer.validate() 基于 validated_data 比较密码一致性
- 密码复杂度：至少 8 位 + 大写字母 + 小写字母 + 数字 + 特殊字符

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m pytest \
        Django_xm/apps/users/tests/test_serializer_validation.py -v --tb=short
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

from Django_xm.apps.users.serializers import (
    PASSWORD_COMPLEXITY_PATTERN,
    UserRegisterSerializer,
)

# ============================================================================
# SubTask 13.5: 密码复杂度校验
# ============================================================================

class UserRegisterSerializerPasswordComplexityTest(unittest.TestCase):
    """密码复杂度校验测试。"""

    def test_valid_complex_password_passes(self) -> None:
        """合法复杂密码通过校验。"""
        s = UserRegisterSerializer(data={
            'username': 'alice01',
            'password': 'ValidP@ss1',
            'password_confirm': 'ValidP@ss1',
        })
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_password_too_short_rejected(self) -> None:
        """密码短于 8 位被拒绝。"""
        short = 'Aa1!aa'  # 6 位
        s = UserRegisterSerializer(data={
            'username': 'bob02',
            'password': short,
            'password_confirm': short,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password', s.errors)

    def test_password_missing_uppercase_rejected(self) -> None:
        """缺少大写字母被拒绝。"""
        pwd = 'lowercase1!'  # 无大写
        s = UserRegisterSerializer(data={
            'username': 'carol03',
            'password': pwd,
            'password_confirm': pwd,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password', s.errors)

    def test_password_missing_lowercase_rejected(self) -> None:
        """缺少小写字母被拒绝。"""
        pwd = 'UPPERCASE1!'  # 无小写
        s = UserRegisterSerializer(data={
            'username': 'dave04',
            'password': pwd,
            'password_confirm': pwd,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password', s.errors)

    def test_password_missing_digit_rejected(self) -> None:
        """缺少数字被拒绝。"""
        pwd = 'NoDigitsHere!'  # 无数字
        s = UserRegisterSerializer(data={
            'username': 'eve05',
            'password': pwd,
            'password_confirm': pwd,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password', s.errors)

    def test_password_missing_special_char_rejected(self) -> None:
        """缺少特殊字符被拒绝。"""
        pwd = 'NoSpecial123'  # 无特殊字符
        s = UserRegisterSerializer(data={
            'username': 'frank06',
            'password': pwd,
            'password_confirm': pwd,
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password', s.errors)

    def test_password_with_various_special_chars_passes(self) -> None:
        """支持特殊字符集中所有字符。"""
        # 每个特殊字符都构造一个合法密码
        special_chars = '!@#$%^&*()_+-=[]{}|;:,.<>?'
        for ch in special_chars:
            pwd = f'Ab1cd{ch}xy'  # 包含大小写、数字、当前特殊字符，长度 8+
            with self.subTest(char=ch):
                s = UserRegisterSerializer(data={
                    'username': f'user_{ord(ch)}',
                    'password': pwd,
                    'password_confirm': pwd,
                })
                self.assertTrue(s.is_valid(), msg=f'char={ch!r}: {s.errors}')

    def test_password_complexity_pattern_constant(self) -> None:
        """PASSWORD_COMPLEXITY_PATTERN 常量已编译。"""
        self.assertTrue(PASSWORD_COMPLEXITY_PATTERN.match('Aa1!bbbb'))
        self.assertFalse(PASSWORD_COMPLEXITY_PATTERN.match('aa1!bbbb'))
        self.assertFalse(PASSWORD_COMPLEXITY_PATTERN.match('AA1!BBBB'))
        self.assertFalse(PASSWORD_COMPLEXITY_PATTERN.match('Aaaa!bbb'))
        self.assertFalse(PASSWORD_COMPLEXITY_PATTERN.match('Aa1bbbbb'))
        self.assertFalse(PASSWORD_COMPLEXITY_PATTERN.match('Aa1!bb'))


# ============================================================================
# SubTask 13.5: 密码一致性校验（基于 validated_data）
# ============================================================================

class UserRegisterSerializerPasswordConfirmTest(unittest.TestCase):
    """密码一致性校验测试。"""

    def test_matching_passwords_passes(self) -> None:
        """两次密码一致通过校验。"""
        s = UserRegisterSerializer(data={
            'username': 'grace07',
            'password': 'ValidP@ss1',
            'password_confirm': 'ValidP@ss1',
        })
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_mismatching_passwords_rejected(self) -> None:
        """两次密码不一致被拒绝。"""
        s = UserRegisterSerializer(data={
            'username': 'henry08',
            'password': 'ValidP@ss1',
            'password_confirm': 'DifferentP@ss2',
        })
        self.assertFalse(s.is_valid())
        # 错误应挂在 password_confirm 字段上（基于 validated_data 比较）
        self.assertIn('password_confirm', s.errors)

    def test_missing_password_confirm_rejected(self) -> None:
        """缺少 password_confirm 字段被拒绝。"""
        s = UserRegisterSerializer(data={
            'username': 'ivan09',
            'password': 'ValidP@ss1',
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password_confirm', s.errors)

    def test_uses_validated_data_not_initial_data(self) -> None:
        """验证逻辑应基于 validated_data（即使两密码本身合法但不同也会被拒）。

        回归点：原代码使用 self.initial_data.get('password') 比较，
        在 password 已通过复杂度校验后才能比较；新代码使用 attrs.get('password')，
        是 validate() 跨字段比较的标准做法。
        """
        # 两个密码都是合法复杂度但内容不同
        s = UserRegisterSerializer(data={
            'username': 'judy10',
            'password': 'ValidP@ss1',
            'password_confirm': 'ValidP@ss2',
        })
        self.assertFalse(s.is_valid())
        self.assertIn('password_confirm', s.errors)


# ============================================================================
# 合法输入端到端通过测试
# ============================================================================

class UserRegisterSerializerHappyPathTest(unittest.TestCase):
    """合法输入端到端通过测试。"""

    def test_full_valid_payload_passes_validation(self) -> None:
        """完整合法 payload 通过 is_valid()。"""
        s = UserRegisterSerializer(data={
            'username': 'ken11',
            'password': 'StrongP@ss1',
            'password_confirm': 'StrongP@ss1',
            'email': 'ken@example.com',
            'mobile': '13800138000',
        })
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data['username'], 'ken11')
        self.assertEqual(s.validated_data['password'], 'StrongP@ss1')

    def test_minimal_valid_payload_passes_validation(self) -> None:
        """最小合法 payload（仅必填字段）通过 is_valid()。"""
        s = UserRegisterSerializer(data={
            'username': 'leo12',
            'password': 'StrongP@ss1',
            'password_confirm': 'StrongP@ss1',
        })
        self.assertTrue(s.is_valid(), msg=str(s.errors))


if __name__ == "__main__":
    unittest.main()
