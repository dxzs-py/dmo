"""extract_thinking_content 双版合并表征测试（G1，phase6a 重复审计）。

合并前锁定现行为：ai_engine/services/thinking.py 与
chat/services/stream_chunk_processors.py 两版函数体逐字相同（唯一差异是
chat 版多一个未使用的 provider_id 参数），对同一批边界输入必须产生完全
一致的输出：
- additional_kwargs["reasoning_content"]（DeepSeek/Ollama）：字符串 / 空串 /
  非字符串 / None / 属性缺失
- content blocks（Anthropic）：dict 块 / LangChain ContentBlock 对象 /
  空 text 回落后续块 / 多段混合 / 无 thinking 块 / 空列表
- content 为普通字符串（含 <think> 标签文本）：当前实现不解析，返回 None
- 非消息对象（无属性）：返回 None
- additional_kwargs 为 None：两版同样抛 AttributeError（getattr 默认值
  不覆盖"属性存在但为 None"）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_extract_thinking_content_parity --settings=Django_xm.settings.test --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.services.thinking import (
    extract_thinking_content as extract_ai_engine,
)
from Django_xm.apps.chat.services.stream_chunk_processors import (
    extract_thinking_content as extract_chat,
)


class _Block:
    """模拟 LangChain ContentBlock 对象（带 type / text 属性）。"""

    def __init__(self, block_type: str, text: str = ""):
        self.type = block_type
        self.text = text


class _Chunk:
    """模拟流式 AIMessageChunk（additional_kwargs / content 属性）。"""

    def __init__(self, additional_kwargs=None, content=None):
        self.additional_kwargs = additional_kwargs if additional_kwargs is not None else {}
        self.content = content


def _outcome(func, chunk):
    """捕获 (状态, 结果/异常类型)，使"两版同样抛错"也可判定一致。"""
    try:
        return ("ok", func(chunk))
    except Exception as e:  # noqa: BLE001 — 表征测试需覆盖任意异常路径
        return ("err", type(e))


def _edge_chunks():
    """边界输入电池：（描述, chunk）列表，覆盖 G1 表征基线。"""
    return [
        ("DeepSeek reasoning 字符串", _Chunk(additional_kwargs={"reasoning_content": "思考片段"})),
        ("reasoning 空串（falsy）", _Chunk(additional_kwargs={"reasoning_content": ""})),
        ("reasoning 非字符串", _Chunk(additional_kwargs={"reasoning_content": 123})),
        ("reasoning None", _Chunk(additional_kwargs={"reasoning_content": None})),
        ("Anthropic dict thinking 块", _Chunk(content=[{"type": "thinking", "text": "claude 思考"}])),
        ("Anthropic 对象 thinking 块", _Chunk(content=[_Block("thinking", "对象思考")])),
        ("dict 块空 text 回落后续块", _Chunk(content=[{"type": "thinking", "text": ""}, {"type": "thinking", "text": "第二段"}])),
        ("对象块空 text 回落后续块", _Chunk(content=[_Block("thinking"), _Block("thinking", "后段")])),
        ("无 thinking 块", _Chunk(content=[{"type": "text", "text": "正文"}])),
        ("多段混合首个命中", _Chunk(content=[{"type": "text"}, {"type": "thinking", "text": "首段"}, _Block("thinking", "次段")])),
        ("content 空列表", _Chunk(content=[])),
        ("content 普通字符串（含 <think> 标签）", _Chunk(content="<think>abc</think>")),
        ("content 空串", _Chunk(content="")),
        ("非消息对象（无属性）", object()),
        ("additional_kwargs 为 None（两版同样抛错）", _Chunk(additional_kwargs=None, content="正文")),
    ]


class ExtractThinkingContentParityTests(SimpleTestCase):
    """G1 表征：两版 extract_thinking_content 同输入输出（含异常路径）一致。"""

    def test_both_versions_agree_on_edge_inputs(self) -> None:
        for desc, chunk in _edge_chunks():
            with self.subTest(desc=desc):
                self.assertEqual(_outcome(extract_ai_engine, chunk), _outcome(extract_chat, chunk))

    def test_no_unused_provider_id_param(self) -> None:
        """合并后契约：权威实现仅接收 chunk 单参数（chat 版原 provider_id
        形参从未被函数体使用，G1 合并时直接去除，不保留兼容签名）。"""
        import inspect

        params = list(inspect.signature(extract_ai_engine).parameters)
        self.assertEqual(params, ["chunk"])

    def test_reasoning_content_string_returned_verbatim(self) -> None:
        """锁定现行为：字符串 reasoning_content 原样返回。"""
        chunk = _Chunk(additional_kwargs={"reasoning_content": "思考"})
        self.assertEqual(extract_ai_engine(chunk), "思考")
        self.assertEqual(extract_chat(chunk), "思考")

    def test_plain_string_content_returns_none(self) -> None:
        """锁定现行为：content 为字符串（含 <think> 标签）不解析，返回 None。"""
        chunk = _Chunk(content="<think>abc</think>")
        self.assertIsNone(extract_ai_engine(chunk))
        self.assertIsNone(extract_chat(chunk))

    def test_thinking_block_text_returned(self) -> None:
        """锁定现行为：dict / 对象两种 thinking 块均提取 text。"""
        dict_chunk = _Chunk(content=[{"type": "thinking", "text": "claude 思考"}])
        obj_chunk = _Chunk(content=[_Block("thinking", "对象思考")])
        self.assertEqual(extract_ai_engine(dict_chunk), "claude 思考")
        self.assertEqual(extract_chat(dict_chunk), "claude 思考")
        self.assertEqual(extract_ai_engine(obj_chunk), "对象思考")
        self.assertEqual(extract_chat(obj_chunk), "对象思考")

    def test_single_source_after_merge(self) -> None:
        """合并后单源锁定：chat 侧各导入路径与 ai_engine 权威实现是同一对象，
        防止副本回潮。"""
        from Django_xm.apps.chat.services.stream_helpers import (
            extract_thinking_content as via_stream_helpers,
        )

        self.assertIs(extract_chat, extract_ai_engine)
        self.assertIs(via_stream_helpers, extract_ai_engine)
