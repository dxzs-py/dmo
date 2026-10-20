"""共享 JSON 摘要工具（context_summary.extract_json_summary）单元测试。

覆盖：dict 每值截断、list[dict] 抽样+字段截断+记录数标注、普通 list 截断、
非 JSON 原样透传、自定义截断参数。
"""

import unittest

from Django_xm.apps.context_manager.services.context_summary import extract_json_summary


class TestExtractJsonSummary(unittest.TestCase):
    def test_dict_truncates_long_values(self):
        text = '{"key": "' + "x" * 400 + '", "short": "ok"}'
        out = extract_json_summary(text)
        self.assertIn("key: " + "x" * 300 + "...", out)
        self.assertIn("short: ok", out)
        self.assertNotIn("x" * 301, out)

    def test_list_of_dicts_marks_record_count_and_truncates(self):
        records = [{"name": "n" * 200, "score": 1} for _ in range(8)]
        out = extract_json_summary(_json_dumps(records))
        self.assertIn("共 8 条记录，字段: name, score", out)
        # 抽样前 5 条
        self.assertEqual(out.count("[0]"), 1)
        self.assertIn("[4]", out)
        self.assertNotIn("[5]", out)
        self.assertIn("... 还有 3 条记录", out)
        # 每字段截 150
        self.assertIn("n" * 150 + "...", out)
        self.assertNotIn("n" * 151, out)

    def test_list_of_dicts_limits_fields(self):
        records = [{"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7", "h": "8"}]
        out = extract_json_summary(_json_dumps(records))
        self.assertNotIn("g=", out)

    def test_plain_list_truncates_and_marks_count(self):
        items = ["i" * 300] * 12
        out = extract_json_summary(_json_dumps(items))
        self.assertIn("共 12 项:", out)
        self.assertIn("i" * 200, out)
        self.assertNotIn("i" * 201, out)
        self.assertIn("... 还有 2 项", out)

    def test_empty_list(self):
        self.assertEqual(extract_json_summary("[]"), "[]")

    def test_non_json_passthrough(self):
        raw = "这是一段普通文本结果，不是 JSON。"
        self.assertEqual(extract_json_summary(raw), raw)

    def test_broken_json_passthrough(self):
        raw = '{"key": broken'
        self.assertEqual(extract_json_summary(raw), raw)

    def test_scalar_json_returns_text(self):
        # 顶层标量（非 dict/list）：原样返回
        self.assertEqual(extract_json_summary("123"), "123")

    def test_custom_params(self):
        text = '{"key": "' + "y" * 50 + '"}'
        out = extract_json_summary(text, dict_value_max_chars=10)
        self.assertIn("y" * 10 + "...", out)
        self.assertNotIn("y" * 11, out)


def _json_dumps(data) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
