"""ANSI 转义序列过滤模块

提供统一的 ANSI 转义序列和 spinner 字符过滤能力，
供所有命令执行类工具（shell_exec 等）复用，避免输出"乱码"假象。

支持的过滤类型：
- CSI 序列：\x1b[ 开头（如 \x1b[?25l、\x1b[K、\x1b[1G、\x1b[0m、\x1b[38;5;200m）
- OSC 序列：\x1b] 开头以 \x07 (BEL) 结尾
- 其他单字符转义：\x1b=、\x1b>
- Unicode spinner 字符：⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏（常见于 ollama/yarn/npm 等进度指示器）
"""

import re

# CSI 序列: ESC [ + 参数字节(0x30-0x3F: 0-9:;<=>?) + 中间字节(0x20-0x2F) + 终止字节(0x40-0x7E)
# 符合 ECMA-48 规范，覆盖 \x1b[?25l \x1b[K \x1b[1G \x1b[0m \x1b[38;5;200m 等全部 CSI 序列
# OSC 序列: ESC ] ... BEL(\x07)
# 其他单字符转义: ESC= / ESC>
_ANSI_ESCAPE_RE = re.compile(
    r'\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]'  # CSI 序列
    r'|\x1b\][^\x07]*\x07'                          # OSC 序列（以 BEL 结尾）
    r'|\x1b[=>]'                                    # 其他单字符转义（ESC=、ESC>）
)

# Unicode spinner 字符（braille 盲文图案，CLI 工具常用作进度旋转动画）
_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_RE = re.compile(f'[{_SPINNER_CHARS}]')


def filter_ansi_codes(text: str) -> str:
    """过滤 ANSI 转义序列（CSI/OSC/其他单字符转义）

    Args:
        text: 可能包含 ANSI 转义序列的原始文本

    Returns:
        已移除 ANSI 转义序列的文本
    """
    if not text:
        return text
    return _ANSI_ESCAPE_RE.sub('', text)


def filter_spinner_chars(text: str) -> str:
    """过滤 Unicode spinner 字符（⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏）

    Args:
        text: 可能包含 spinner 字符的原始文本

    Returns:
        已移除 spinner 字符的文本
    """
    if not text:
        return text
    return _SPINNER_RE.sub('', text)


def clean_tool_output(text: str) -> str:
    """统一清理工具输出：过滤 ANSI 转义序列 + spinner 字符

    作为所有命令执行类工具的统一入口，先过滤 ANSI 转义码，
    再清理 spinner 残留的 Unicode 字符。

    Args:
        text: 命令执行的原始输出

    Returns:
        已清理的输出文本
    """
    if not text:
        return text
    return filter_spinner_chars(filter_ansi_codes(text))
