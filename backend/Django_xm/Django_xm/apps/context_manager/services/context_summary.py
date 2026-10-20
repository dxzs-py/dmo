"""结构化 JSON 摘要工具（共享模块，无业务依赖）。

从 ``retrieval_augmenter.py`` 的成熟压缩策略抽取，供两处复用：
- context_manager：RAG 工具结果后处理（大型 JSON 提取关键信息）；
- agent_hub：``wait_for_subagent`` fan-in 汇总预算压缩（子代理结果进入父上下文前收敛）。

压缩策略（对 JSON 输入无损结构、对内容有损）：
- dict：每值截断（默认 300 字符）逐键一行；
- list[dict]：标注总记录数与字段名，抽样前 N 条（默认 5），每字段截断（默认 150）；
- list（非 dict 项）：每项截断（默认 200 字符）抽样前 N 项（默认 10）；
- 非 JSON 文本原样返回（不做有损处理，由调用方按自身预算硬截断）。

放置依据：import-linter 无 agent_hub → context_manager 的禁止契约，
agent_hub.subagent_tools.wait 可直接导入本模块。
"""

from __future__ import annotations

import json

DEFAULT_DICT_VALUE_MAX_CHARS = 300
DEFAULT_LIST_ITEM_MAX_CHARS = 150
DEFAULT_LIST_ITEM_PREVIEW_COUNT = 5
DEFAULT_PLAIN_ITEM_MAX_CHARS = 200
DEFAULT_PLAIN_ITEM_PREVIEW_COUNT = 10
# list[dict] 抽样时每条记录最多展示的字段数（防宽表记录撑爆摘要）。
DEFAULT_LIST_ITEM_FIELD_COUNT = 6


def _truncate(value: str, max_chars: int) -> str:
    """超长截断并追加省略号标记。"""
    if len(value) > max_chars:
        return value[:max_chars] + "..."
    return value


def extract_json_summary(
    text: str,
    *,
    dict_value_max_chars: int = DEFAULT_DICT_VALUE_MAX_CHARS,
    list_item_max_chars: int = DEFAULT_LIST_ITEM_MAX_CHARS,
    list_item_preview_count: int = DEFAULT_LIST_ITEM_PREVIEW_COUNT,
    plain_item_max_chars: int = DEFAULT_PLAIN_ITEM_MAX_CHARS,
    plain_item_preview_count: int = DEFAULT_PLAIN_ITEM_PREVIEW_COUNT,
) -> str:
    """将 JSON 文本压缩为结构化摘要；非 JSON 输入原样返回。

    Args:
        text: 待压缩文本（JSON 对象/数组，或任意文本）。
        dict_value_max_chars: dict 每个值的截断长度。
        list_item_max_chars: list[dict] 每条记录每字段的截断长度。
        list_item_preview_count: list[dict] 抽样展示的记录条数。
        plain_item_max_chars: 普通 list 每项的截断长度。
        plain_item_preview_count: 普通 list 抽样展示的项数。

    Returns:
        压缩后的摘要文本（list[dict] 自带"共 N 条记录"标注）；解析失败时返回原文。
    """
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return text

    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            lines.append(f"{key}: {_truncate(str(value), dict_value_max_chars)}")
        return "\n".join(lines)

    if isinstance(data, list):
        if not data:
            return "[]"
        if isinstance(data[0], dict):
            keys = list(data[0].keys())
            lines = [f"共 {len(data)} 条记录，字段: {', '.join(keys)}"]
            for i, item in enumerate(data[:list_item_preview_count]):
                item_parts = []
                for k in keys[:DEFAULT_LIST_ITEM_FIELD_COUNT]:
                    item_parts.append(f"{k}={_truncate(str(item.get(k, '')), list_item_max_chars)}")
                lines.append(f"  [{i}] {', '.join(item_parts)}")
            if len(data) > list_item_preview_count:
                lines.append(f"  ... 还有 {len(data) - list_item_preview_count} 条记录")
            return "\n".join(lines)
        items = [_truncate(str(item), plain_item_max_chars) for item in data[:plain_item_preview_count]]
        result = f"共 {len(data)} 项:\n" + "\n".join(items)
        if len(data) > plain_item_preview_count:
            result += f"\n... 还有 {len(data) - plain_item_preview_count} 项"
        return result

    return text
