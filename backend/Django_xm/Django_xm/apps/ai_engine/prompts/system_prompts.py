"""
系统提示词模板模块
定义各种场景下的系统提示词，用于指导 AI 的行为

提示词内容存储在 prompts.yaml 中，本模块负责加载和格式化。
"""

from typing import Dict, Optional
from datetime import datetime
from pathlib import Path

import yaml


_PROMPTS_FILE = Path(__file__).parent / "prompts.yaml"


def _load_prompts() -> Dict[str, Optional[str]]:
    """从 YAML 文件加载提示词"""
    if _PROMPTS_FILE.exists():
        with open(_PROMPTS_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    return {}


SYSTEM_PROMPTS: Dict[str, Optional[str]] = _load_prompts()


WRITER_GUIDELINES = (
    "组织内容时根据主题动态选择结构；避免僵化模板。"
    "以概念与动机开始，随后给出核心用法与API，"
    "提供真实示例或代码片段，总结最佳实践与常见陷阱，"
    "必要时加入对比与FAQ。"
    "强调信息整合与洞察表达，避免机械化标题与占位语。"
    "引用权威来源并使用内联引用与参考列表。"
)


def get_system_prompt(
    mode: str = "default",
    custom_instructions: Optional[str] = None,
    include_time: bool = True,
) -> str:
    if mode not in SYSTEM_PROMPTS:
        available_modes = ", ".join(SYSTEM_PROMPTS.keys())
        raise ValueError(f"未知的提示词模式: {mode}. 可用模式: {available_modes}")

    prompt = SYSTEM_PROMPTS[mode]
    if prompt is None:
        prompt = SYSTEM_PROMPTS["default"]

    if include_time:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt = prompt.format(current_time=current_time)
    else:
        prompt = prompt.replace("当前时间：{current_time}\n\n", "")

    if custom_instructions:
        prompt += f"\n\n补充说明：\n{custom_instructions}"

    return prompt


def create_custom_prompt(
    role: str,
    capabilities: list,
    principles: list,
    additional_context: Optional[str] = None,
) -> str:
    prompt_parts = [f"你是 {role}。"]

    if capabilities:
        prompt_parts.append("\n你的能力：")
        for i, cap in enumerate(capabilities, 1):
            prompt_parts.append(f"{i}. {cap}")

    if principles:
        prompt_parts.append("\n你的准则：")
        for principle in principles:
            prompt_parts.append(f"- {principle}")

    if additional_context:
        prompt_parts.append(f"\n{additional_context}")

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt_parts.append(f"\n当前时间：{current_time}")

    return "\n".join(prompt_parts)


TOOL_USAGE_INSTRUCTIONS = """
可用工具说明（分为两类：内置工具 和 MCP 工具）：

【内置工具】— 本地直接执行的基础工具：
- 🕐 get_current_time / get_current_date: 获取当前时间和日期
- 🧮 calculator: 执行数学计算
- 🌐 translate_text: 翻译文本到指定语言
- 🌐 detect_language: 检测文本的语言
- 🌤️ weather_query: 查询城市天气（实时/预报）
- 🔍 web_search / duckduckgo_search: 搜索互联网获取最新信息
- 🌐 web_fetch: 抓取网页内容并转换为纯文本
- 📄 file_reader: 读取指定路径的文件内容
- 📎 attachment_reader: 读取用户上传的聊天附件内容
- 📁 fs_write_file / fs_read_file / fs_list_files / fs_search_files: 文件系统操作
- ✅ todo_write / todo_read: 任务管理（创建/读取待办事项）
- 🤖 agent_create / agent_run / agent_list: 子代理管理（创建/执行/列出子代理任务）
- 📚 knowledge_base_*: 搜索知识库中的相关信息

【MCP 工具】— 通过 MCP (Model Context Protocol) 协议连接的外部工具服务：
{mcp_tools_section}

重要区分：
- "MCP 工具"特指通过 MCP 协议连接的外部服务提供的工具，如 sequentialthinking、resolve-library-id、query-docs 等
- 其他工具（如 calculator、weather_query、web_search 等）是本地内置工具，不是 MCP 工具
- 当用户询问"你可以使用什么 MCP 工具"时，只应列出【MCP 工具】分类下的工具

使用工具的时机：
- 需要最新信息或实时数据时，使用 web_search 或 duckduckgo_search
- 需要知道当前时间或日期时，使用 get_current_time
- 需要精确计算时，使用 calculator
- 查询天气时，使用 weather_query（不需要先获取时间）
- 需要翻译文本时，使用 translate_text
- 需要检测语言时，使用 detect_language
- 用户上传了文件并基于文件提问时，使用 attachment_reader 读取文件内容
- 需要读取服务器上的文件时，使用 file_reader
- 需要获取网页内容时，使用 web_fetch
- 需要管理任务列表时，使用 todo_write 和 todo_read
- 需要委派子任务给独立代理时，使用 agent_create 和 agent_run
- 需要分步骤深度推理时，使用 sequentialthinking
- 需要查询编程库/框架的最新文档时，先用 resolve-library-id 解析库 ID，再用 query-docs 查询文档
- 需要查询项目信息或系统状态时，使用 project_info 或 system_status

重要提示：
- 优先使用工具获取准确信息
- 避免重复调用工具
- 查询天气不需要先调用时间工具
- 翻译时只需指定目标语言，源语言会自动检测
- 用户消息中如果包含文件内容，直接基于该内容回答即可
- web_fetch 可以获取网页内容，适用于需要读取特定URL信息的场景
- 子代理适合处理独立的子任务，如探索、规划、验证等

【工具使用通用规范（适用于所有工具，不仅 fs_*）】
1. **避免无意义重复调用**：任何工具（calculator / web_search / weather_query / knowledge_base_* / fs_* 等）
   在短时窗口内对相同资源执行相同操作将被系统自动跳过（DEDUP），无需你显式记忆调用历史
2. **检测到循环时停止**：如果系统提示"工具 X 对资源 Y 已连续 N 次调用"（BLOCK 事件），
   立即停止调用 X，基于已有结果回复用户或换一种思路
3. **写文件特殊规则**（fs_write_file）：
   - **写前先读**：若目标文件已存在，先用 fs_read_file 读取当前内容，确认是否需要覆盖
   - **避免无意义重写**：如果 fs_read_file 返回的内容已包含你打算写入的相同/相似内容，不要再次调用 fs_write_file
   - **写后即报告**：成功写入文件后，向用户报告"已写入文件 X，共 Y 字符"并停止工具调用
   - **写完即停**：完成用户核心需求后立刻用自然语言回复并结束，不要"打磨"或"再完善"同一文件
   - **支持分章节/增量更新**：合理场景下可多次写入同一文件，但每次应是**实质性的内容变化**
   - 系统会自动跳过短时窗口内完全相同内容的重复写入（去重）
   - 若内容变化很小（< 5%），系统会判定为打磨循环并提示阻断
4. **读/搜索类工具**（fs_read_file / web_fetch / knowledge_base_search 等）：
   - 同资源（path / url / query）在短时窗口内重复调用会被去重
   - 若结果可能已变化（如时间敏感数据），应明确表达"基于缓存"或换用其他工具
5. **任务完成后停止**：用户的核心需求满足后立即结束，不要追加无关优化

【文件操作（fs_write_file / fs_read_file）行为规范】
1. **写前先读**：调用 fs_write_file 写入前，若目标文件已存在，先用 fs_read_file 读取当前内容，确认是否需要覆盖
2. **避免无意义重写**：如果 fs_read_file 返回的内容已包含你打算写入的相同/相似内容，不要再次调用 fs_write_file
3. **写后即报告**：成功写入文件后，向用户报告"已写入文件 X，共 Y 字符"并停止工具调用
4. **写完即停**：完成用户核心需求后立刻用自然语言回复并结束，不要"打磨"或"再完善"同一文件
5. **支持分章节/增量更新**：如果是分章节写作、增量更新、内容修订等合理场景，可多次写入同一文件
   - 但每次写入应是**实质性的内容变化**，而非微调措辞
   - 系统会自动跳过短时窗口内完全相同内容的重复写入（去重）
   - 若内容变化很小（< 5%），系统会判定为打磨循环并提示阻断
6. **任务完成后停止**：用户的核心需求满足后立即结束，不要追加无关优化

知识库工具使用规范（重要）：
- knowledge_base_* 工具返回的检索结果包含知识库信息和相关文档内容，你必须基于这些内容回答用户问题
- 回答用户问题时，必须忠实于检索结果中的内容，绝不允许编造或推断
- 每个要点必须保留其领域上下文
- 如果用户要求总结知识库内容，应基于检索结果给出完整的主题概览
- 只在必要时引用关键片段作为佐证，且引用部分不超过3-5行
- 如果检索结果很长，提取与用户问题直接相关的要点
"""


def get_prompt_with_tools(mode: str = "default", mcp_tools_section: str = "（当前未加载 MCP 工具）") -> str:
    base_prompt = get_system_prompt(mode)
    tool_instructions = TOOL_USAGE_INSTRUCTIONS.format(mcp_tools_section=mcp_tools_section)
    return f"{base_prompt}\n\n{tool_instructions}"


def build_dynamic_prompt(
    mode: str = "default",
    context: Optional[str] = None,
    custom_instructions: Optional[str] = None,
) -> str:
    prompt = get_system_prompt(mode, custom_instructions=custom_instructions)
    if context:
        prompt = f"{prompt}\n\n{context}"
    return prompt
