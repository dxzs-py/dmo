# -*- coding: utf-8 -*-
"""临时脚本：修复 stream_helpers.py 的两个 bug（Task 2 + Task 3）。完成后自删。"""
import ast
import io
import sys

FILE = r"d:\programming\langchain\langchain_xm\backend\Django_xm\Django_xm\apps\chat\services\stream_helpers.py"

# ---------- Bug 1: _publish_tool_lifecycle_event parameters 字段 ----------
OLD1 = """    parameters = tool_info.get('parameters')
    # 只有非空 dict 才包含 parameters 字段
    # 空 {} 不包含,避免前端 isNonEmptyParams 保护逻辑失效
    # （{} 是 truthy 但应视为空,前端收到 {} 会覆盖本地已有的非空参数）
    # 前端 addOrUpdateToolCallInMap 中 isNonEmptyParams(data.parameters) 对 {} 返回 false,
    # 会回退到 existing.parameters,因此不发布 parameters 字段是安全的
    if parameters is not None and isinstance(parameters, dict) and len(parameters) > 0:
        payload['parameters'] = parameters
"""

NEW1 = """    parameters = tool_info.get('parameters')
    # TOOL_CALL_INPUT_READY 事件 MUST 始终包含 parameters 字段（即使为空 dict {}）
    # 原因：event_schema.py 中 TOOL_CALL_INPUT_READY 的 _REQUIRED_FIELDS 要求 parameters 必填
    # 之前只在 len(parameters) > 0 时才包含，导致无参数工具事件被校验失败丢弃（问题11根因）
    # 其他事件类型（PENDING/RUNNING/COMPLETED 等）仅在非空时包含，避免覆盖前端已有参数
    # 原 isNonEmptyParams 保护逻辑说明（仅适用于非 INPUT_READY 事件）：
    #   空 {} 不包含,避免前端 isNonEmptyParams 保护逻辑失效
    #   （{} 是 truthy 但应视为空,前端收到 {} 会覆盖本地已有的非空参数）
    #   前端 addOrUpdateToolCallInMap 中 isNonEmptyParams(data.parameters) 对 {} 返回 false,
    #   会回退到 existing.parameters,因此不发布 parameters 字段是安全的
    if event_type == EventType.TOOL_CALL_INPUT_READY:
        # INPUT_READY 事件始终包含 parameters（即使是空 dict {}）
        payload['parameters'] = parameters if isinstance(parameters, dict) else {}
    elif parameters is not None and isinstance(parameters, dict) and len(parameters) > 0:
        # 其他事件类型仅非空时包含
        payload['parameters'] = parameters
"""

# ---------- Bug 2: merge_existing_approval_fields ----------
OLD2 = '''def merge_existing_approval_fields(
    persisted_tool_calls: List[Dict[str, Any]],
    existing_tool_calls: List[Dict[str, Any]],
) -> None:
    """合并 existing_tool_calls 中已有的 approval 字段到 persisted_tool_calls（就地修改）。

    build_persisted_tool_calls 不包含 approval 字段，直接覆盖 msg.tool_calls 会丢失
    sync_approval_state_to_chat_message 更新的审批状态，导致刷新后审批状态回退。

    匹配策略：按 tool_call id（tc['id'] 或 tc['tool_call_id']）对齐，
    仅当 persisted 条目缺少 approval 字段时从 existing 补充，已有 approval 不被覆盖。
    """
    if not isinstance(persisted_tool_calls, list) or not isinstance(existing_tool_calls, list):
        return
    if not persisted_tool_calls or not existing_tool_calls:
        return
    if len(persisted_tool_calls) != len(existing_tool_calls):
        return
    for new_tc, old_tc in zip(persisted_tool_calls, existing_tool_calls):
        if not isinstance(old_tc, dict) or not isinstance(new_tc, dict):
            continue
        if old_tc.get('approval') and not new_tc.get('approval'):
            new_tc['approval'] = old_tc['approval']
'''

NEW2 = '''def merge_existing_approval_fields(
    persisted_tool_calls: List[Dict[str, Any]],
    existing_tool_calls: List[Dict[str, Any]],
) -> None:
    """合并 existing_tool_calls 中已有的 approval 字段到 persisted_tool_calls（就地修改）。

    build_persisted_tool_calls 不包含 approval 字段，直接覆盖 msg.tool_calls 会丢失
    sync_approval_state_to_chat_message 更新的审批状态，导致刷新后审批状态回退。

    匹配策略：按 tool_call_id（优先）或 id（降级）构建索引，
    遍历 persisted_tool_calls 在 existing 索引中查找对应工具，
    仅当 persisted 条目缺少 approval 字段时从 existing 补充，已有 approval 不被覆盖。
    长度不等时仅合并交集部分（新增工具保留 persisted 原值，不强制跳过）。
    """
    if not isinstance(persisted_tool_calls, list) or not isinstance(existing_tool_calls, list):
        return
    if not persisted_tool_calls or not existing_tool_calls:
        return

    # 按 tool_call_id（优先）或 id（降级）构建 existing 索引
    existing_index: Dict[str, Dict[str, Any]] = {}
    for old_tc in existing_tool_calls:
        if not isinstance(old_tc, dict):
            continue
        tc_id = old_tc.get('tool_call_id') or old_tc.get('id')
        if tc_id and old_tc.get('approval'):
            existing_index[str(tc_id)] = old_tc

    if not existing_index:
        return

    # 遍历 persisted，按 id 查找 existing 中对应工具的 approval 字段
    for new_tc in persisted_tool_calls:
        if not isinstance(new_tc, dict):
            continue
        # 已有 approval 不被覆盖
        if new_tc.get('approval'):
            continue
        tc_id = new_tc.get('tool_call_id') or new_tc.get('id')
        if not tc_id:
            continue
        old_tc = existing_index.get(str(tc_id))
        if old_tc and old_tc.get('approval'):
            new_tc['approval'] = old_tc['approval']
'''


def main():
    with io.open(FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # 校验原始内容包含待替换片段
    c1 = content.count(OLD1)
    c2 = content.count(OLD2)
    print(f"[CHECK] OLD1 occurrences={c1}, OLD2 occurrences={c2}")
    if c1 != 1 or c2 != 1:
        print("[FAIL] 待替换片段未唯一命中，终止。", file=sys.stderr)
        sys.exit(1)

    new_content = content.replace(OLD1, NEW1, 1).replace(OLD2, NEW2, 1)

    # 语法校验
    try:
        ast.parse(new_content)
    except SyntaxError as e:
        print(f"[FAIL] 语法校验失败: {e}", file=sys.stderr)
        sys.exit(2)

    with io.open(FILE, 'w', encoding='utf-8', newline='') as f:
        f.write(new_content)

    # 简单 diff 摘要
    old_lines = content.splitlines()
    new_lines = new_content.splitlines()
    print(f"[OK] 旧行数={len(old_lines)} 新行数={len(new_lines)}")