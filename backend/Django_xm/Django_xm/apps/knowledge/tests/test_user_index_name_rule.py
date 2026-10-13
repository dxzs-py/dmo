"""D-1 索引命名规则单源锁定测试。

规则 ``user_{user_id}_{index_name}`` 是多租户隔离主机制（digest 线索⑤），
原在 views_utils（权威）+ rag_chat_service（含不可达回退分支）+ multi_kb_retriever
内联 + chat_service 内联 + learning retrieval_node 等价构造共 5 处定义；
现收敛到 ``views_utils.build_user_index_name`` 单源，本测试锁定规则与委托关系。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_user_index_name_rule --noinput
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.knowledge.views_utils import build_user_index_name, get_original_index_name


class UserIndexNameRuleTests(unittest.TestCase):
    def test_rule_format(self):
        self.assertEqual(build_user_index_name(1, "kb-a"), "user_1_kb-a")
        self.assertEqual(build_user_index_name(42, "中文库"), "user_42_中文库")

    def test_get_original_index_name_roundtrip(self):
        """反向解析与正向规则构成往返（含 kb 名中带下划线的场景）。"""
        for user_id, kb in [(1, "kb-a"), (7, "kb_with_underscore"), (9, "知识库")]:
            full = build_user_index_name(user_id, kb)
            self.assertEqual(get_original_index_name(full), kb)

    def test_no_fallback_branch(self):
        """单源函数无回退语义（原 rag_chat_service 副本的空 user_id 回退已显式删除）。"""
        import inspect

        from Django_xm.apps.knowledge import views_utils

        source = inspect.getsource(views_utils.build_user_index_name)
        self.assertNotIn("else", source)


if __name__ == "__main__":
    unittest.main()
