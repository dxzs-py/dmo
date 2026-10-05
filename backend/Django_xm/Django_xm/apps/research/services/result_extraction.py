"""研究结果提取边界模块（E6 三边界拆分：result_extraction）。

职责（audit B 组 §2.3-1）：
- ``extract_files_info``：从图执行结果消息的 tool_calls 提取 write_file 文件记录
  （原 ``OfficialDeepAgentAdapter._extract_files_info`` 静态方法原样迁移）。
- ``scan_disk_files``：扫描 work_dir 下 notes/plans/reports 全文进内存
  （原 ``OfficialDeepAgentAdapter._scan_disk_files`` 方法原样迁移，work_dir 参数化）。
- ``apply_short_report_disk_fallback``：短回复磁盘兜底——原 adapter 内 3 处逐字
  重复的兜底块（research() / aresearch() / astream_research_with_interrupts()
  终态提取各一处）参数化归一为本函数。

全部为纯函数（仅读 work_dir 文件系统），零跨 app 依赖，可独立单测。
被 ``adapter.py`` 消费（3 处兜底调用点 + 终态提取）。
"""

import os
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def scan_disk_files(work_dir: str | None) -> dict[str, Any]:
    """扫描 work_dir 下 notes/plans/reports 子目录的文本文件全文。

    原样迁移自 ``OfficialDeepAgentAdapter._scan_disk_files``（self.work_dir → 参数）。

    Args:
        work_dir: 深度研究工作目录（None/不存在时返回空 dict）

    Returns:
        rel_path（正斜杠归一）→ {"content": 全文, "encoding": "utf-8"}；
        二进制/不可读文件静默跳过（UnicodeDecodeError/OSError）。
    """
    if not work_dir or not os.path.isdir(work_dir):
        return {}
    files = {}
    for subdir in ("notes", "plans", "reports"):
        sub_path = os.path.join(work_dir, subdir)
        if not os.path.isdir(sub_path):
            continue
        for root, _dirs, fnames in os.walk(sub_path):
            for fname in fnames:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, work_dir).replace("\\", "/")
                try:
                    with open(full_path, encoding="utf-8") as f:
                        content = f.read()
                    files[rel_path] = {
                        "content": content,
                        "encoding": "utf-8",
                    }
                except (UnicodeDecodeError, OSError):
                    pass
    if files:
        logger.info(f"[OfficialDeepAgent] 从磁盘扫描到 {len(files)} 个文件: {list(files.keys())}")
    return files


def extract_files_info(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从图执行结果消息的 tool_calls 提取 write_file 文件记录。

    原样迁移自 ``OfficialDeepAgentAdapter._extract_files_info`` 静态方法。

    Args:
        result: 图执行最终状态（含 messages）

    Returns:
        [{"path", "name", "type": "file", "size"(content 长度)}]（按出现顺序）
    """
    files = []
    messages = result.get("messages", [])
    for msg in messages:
        if hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if tc.get("name") == "write_file":
                    args = tc.get("args", {})
                    path = args.get("path", "")
                    if path:
                        files.append(
                            {
                                "path": path,
                                "name": os.path.basename(path),
                                "type": "file",
                                "size": len(args.get("content", "")),
                            }
                        )
    return files


def apply_short_report_disk_fallback(final_report: str, disk_files: dict[str, Any]) -> str:
    """短回复磁盘兜底（原 adapter 内 3 处逐字重复的兜底块参数化归一，行为等价）。

    当 AI 回复过短时（通常是确认语），按优先级从磁盘文件提取完整报告：
    ``reports/final_report.md`` → ``final_report.md``；仅当候选文件正文比当前
    回复更长时替换；命中第一个存在的候选 key 后即停止（无论是否替换）。

    Args:
        final_report: 当前提取的最终回复文本
        disk_files: ``scan_disk_files`` 产出（rel_path → {content, encoding}）

    Returns:
        替换后（或原样）的最终回复文本
    """
    if len(final_report.strip()) < 200 and disk_files:
        for report_key in ("reports/final_report.md", "final_report.md"):
            if report_key in disk_files:
                report_content = disk_files[report_key].get("content", "")
                if len(report_content.strip()) > len(final_report.strip()):
                    final_report = report_content
                    logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                break
    return final_report
