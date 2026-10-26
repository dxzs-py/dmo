"""引用真实性校验（Phase 2）回归。

锁定：
1. 明确 unsupported 的引用编号被移除，正文不改写；
2. 全部 supported / 无引用 / 越界编号 → 原答案返回；
3. 解析失败 / LLM 异常 → 原答案返回且 skipped=True（误判保护）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_citation_verifier --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.knowledge.services.citation_verifier import verify_answer_citations


class CitationVerifierTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"content": "LangChain 是 LLM 应用开发框架", "source": "a.md"},
            {"content": "Redis 是内存数据库", "source": "b.md"},
        ]

    @staticmethod
    def _llm(payload: str):
        llm = mock.MagicMock()
        llm.invoke.return_value = mock.MagicMock(content=payload)
        return llm

    def test_removes_unsupported_citation(self):
        answer = "LangChain 是框架[1]。Redis 是关系型数据库[2]。"
        llm = self._llm('[{"id":1,"supported":true},{"id":2,"supported":false}]')

        cleaned, stats = verify_answer_citations(answer, self.sources, llm=llm)

        self.assertIn("[1]", cleaned)
        self.assertNotIn("[2]", cleaned)
        self.assertEqual(stats["checked"], 2)
        self.assertEqual(stats["unsupported"], 1)
        self.assertFalse(stats["skipped"])

    def test_all_supported_keeps_answer(self):
        answer = "LangChain 是框架[1]。"
        llm = self._llm('[{"id":1,"supported":true}]')

        cleaned, stats = verify_answer_citations(answer, self.sources, llm=llm)

        self.assertEqual(cleaned, answer)
        self.assertEqual(stats["unsupported"], 0)

    def test_parse_failure_keeps_answer(self):
        answer = "LangChain 是框架[1]。"
        llm = self._llm("这不是 JSON")

        cleaned, stats = verify_answer_citations(answer, self.sources, llm=llm)

        self.assertEqual(cleaned, answer)
        self.assertTrue(stats["skipped"])

    def test_llm_exception_keeps_answer(self):
        answer = "LangChain 是框架[1]。"
        llm = mock.MagicMock()
        llm.invoke.side_effect = RuntimeError("timeout")

        cleaned, stats = verify_answer_citations(answer, self.sources, llm=llm)

        self.assertEqual(cleaned, answer)
        self.assertTrue(stats["skipped"])

    def test_no_citation_skips_llm(self):
        llm = mock.MagicMock()

        cleaned, stats = verify_answer_citations("没有引用的答案。", self.sources, llm=llm)

        self.assertEqual(cleaned, "没有引用的答案。")
        llm.invoke.assert_not_called()
        self.assertEqual(stats["checked"], 0)

    def test_out_of_range_citation_not_checked(self):
        llm = mock.MagicMock()

        cleaned, stats = verify_answer_citations("越界引用[9]。", self.sources, llm=llm)

        self.assertEqual(cleaned, "越界引用[9]。")
        llm.invoke.assert_not_called()
        self.assertEqual(stats["checked"], 0)

    def test_max_sentences_limits_checked(self):
        answer = "".join(f"陈述{i}[1]。" for i in range(10))
        llm = self._llm('[{"id":1,"supported":true},{"id":2,"supported":true}]')

        _, stats = verify_answer_citations(answer, self.sources, llm=llm, max_sentences=2)

        self.assertEqual(stats["checked"], 2)


if __name__ == "__main__":
    unittest.main()
