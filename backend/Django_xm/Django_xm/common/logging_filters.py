"""日志敏感信息脱敏过滤器。

QueryParamTokenAuthentication（apps/core/authentication.py）允许用查询串传 JWT
（SSE / 文件下载无法设置 Authorization 头所必需），代价是 JWT 会进入日志：
django.request 在 4xx/5xx 记录完整路径（含查询串），本项目多处日志也会打印带
查询串的 URL。JWT 泄漏即可冒用，等同凭据泄漏。

本过滤器挂在 handler 上，对该 handler 输出的全部日志做兜底脱敏（含 django.request），
格式化前完成，formatter 无需改动，零功能风险。
不覆盖：浏览器历史 / Referer / nginx access log（需另行处理）。
"""

import logging
import re
from contextlib import suppress

MASK = "***"

# 查询串敏感键：保留键名，抹掉值（token=xxx / access_token=xxx / refresh_token=xxx）
_QUERY_SECRET_RE = re.compile(r"(?i)(token|access_token|refresh_token)=([^& ]+)")
# Authorization: Bearer <token>
_BEARER_RE = re.compile(r"(?i)(bearer) ([A-Za-z0-9._-]+)")


def redact_sensitive(text):
    """把字符串中的令牌类敏感值替换为 ***。"""
    if not text:
        return text
    text = _QUERY_SECRET_RE.sub(lambda m: m.group(1) + "=" + MASK, text)
    return _BEARER_RE.sub(lambda m: m.group(1) + " " + MASK, text)


class RedactSensitiveFilter(logging.Filter):
    """对日志记录的消息与参数做敏感值脱敏。脱敏失败绝不打断日志本身。"""

    def filter(self, record):
        with suppress(Exception):  # pragma: no cover
            try:
                rendered = record.getMessage()
            except Exception:
                rendered = str(record.msg)
            record.msg = redact_sensitive(rendered)
            record.args = ()  # 防止 formatter 二次拼接泄漏原值
        return True
