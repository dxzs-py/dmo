"""日志脱敏过滤器回归测试。

QueryParamTokenAuthentication 允许 ?token=<JWT>，若日志不脱敏则凭据泄漏。
"""

import logging
import unittest

from Django_xm.common.logging_filters import RedactSensitiveFilter, redact_sensitive


class TestRedactSensitive(unittest.TestCase):
    def test_query_token_masked(self):
        self.assertEqual(redact_sensitive("/x/?token=abc.def&path=/y"), "/x/?token=***&path=/y")

    def test_access_and_refresh_token_masked(self):
        out = redact_sensitive("a?access_token=AAA&refresh_token=BBB")
        self.assertNotIn("AAA", out)
        self.assertNotIn("BBB", out)

    def test_bearer_masked(self):
        self.assertEqual(redact_sensitive("Bearer abc.def-ghi"), "Bearer ***")

    def test_plain_message_untouched(self):
        self.assertEqual(redact_sensitive("plain message"), "plain message")

    def test_filter_masks_and_clears_args(self):
        rec = logging.LogRecord("n", logging.INFO, "p", 1, "url=%s", ("/x/?token=SECRET",), None)
        self.assertTrue(RedactSensitiveFilter().filter(rec))
        self.assertNotIn("SECRET", rec.getMessage())

    def test_filter_survives_bad_format(self):
        rec = logging.LogRecord("n", logging.INFO, "p", 1, "%d items", ("not-an-int",), None)
        self.assertTrue(RedactSensitiveFilter().filter(rec))


if __name__ == "__main__":
    unittest.main()
