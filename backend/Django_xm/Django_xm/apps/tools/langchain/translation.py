import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

LANGUAGE_MAP = {
    "中文": "Chinese",
    "英语": "English",
    "日语": "Japanese",
    "韩语": "Korean",
    "法语": "French",
    "德语": "German",
    "西班牙语": "Spanish",
    "葡萄牙语": "Portuguese",
    "俄语": "Russian",
    "阿拉伯语": "Arabic",
    "意大利语": "Italian",
    "泰语": "Thai",
    "越南语": "Vietnamese",
    "印尼语": "Indonesian",
    "马来语": "Malay",
    "荷兰语": "Dutch",
    "波兰语": "Polish",
    "土耳其语": "Turkish",
    "印地语": "Hindi",
    "乌克兰语": "Ukrainian",
}


def _normalize_language(lang: str) -> str:
    return LANGUAGE_MAP.get(lang, lang)


class TranslateTextInput(BaseModel):
    text: str = Field(description="要翻译的文本内容")
    target_language: str = Field(description="目标语言，如'英语'、'中文'、'日语'、'法语'等")
    source_language: str | None = Field(default=None, description="源语言（可选，不指定则自动检测）")


class DetectLanguageInput(BaseModel):
    text: str = Field(description="要检测语言的文本")


class TranslateTextTool(BaseTool):
    name: str = "translate_text"
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "translation"}
    )
    description: str = (
        "将文本翻译为指定语言，支持中英日韩法德西葡俄等20+种语言互译。"
        "适用场景：用户需要翻译文本、切换对话语言、或用不同语言交流时使用。"
        "不适用：语言检测（应使用 detect_language）、数学计算、网络搜索。"
        "参数：text-要翻译的文本内容（必填），target_language-目标语言（必填，如'英语'、'中文'、'日语'、'法语'），"
        "source_language-源语言（可选，不指定则自动检测）。"
        "边界：翻译质量依赖 LLM 模型能力，超长文本可能被截断。"
    )
    args_schema: type[BaseModel] = TranslateTextInput

    def _run(self, text: str, target_language: str, source_language: str | None = None) -> str:
        logger.info(f"🌐 翻译请求: target={target_language}, source={source_language or 'auto'}")

        target = _normalize_language(target_language)

        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

            model = get_chat_model(temperature=0.3)

            if source_language:
                source = _normalize_language(source_language)
                prompt = f"请将以下{source}文本翻译为{target}。只返回翻译结果，不要添加解释或注释。\n\n原文：{text}"
            else:
                prompt = (
                    f"请将以下文本翻译为{target}。"
                    f"自动检测原文语言，只返回翻译结果，不要添加解释或注释。\n\n"
                    f"原文：{text}"
                )

            response = model.invoke([{"role": "user", "content": prompt}])
            translated = getattr(response, "content", "")

            if not translated:
                return "翻译失败：未获取到翻译结果"

            logger.info(f"🌐 翻译完成: {len(translated)} 字符")
            return translated.strip()

        except Exception as e:
            error_msg = f"翻译失败: {e!s}"
            logger.exception(error_msg)
            return error_msg

    async def _arun(self, text: str, target_language: str, source_language: str | None = None) -> str:
        return self._run(text=text, target_language=target_language, source_language=source_language)


class DetectLanguageTool(BaseTool):
    name: str = "detect_language"
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "translation"}
    )
    description: str = (
        "检测文本使用的语言，返回语言的中文名称（如'中文'、'英语'、'日语'）。"
        "适用场景：需要识别文本使用的是什么语言、在翻译前确定源语言时使用。"
        "不适用：翻译文本（应使用 translate_text）、数学计算、网络搜索。"
        "参数：text-要检测语言的文本（必填）。"
        "边界：检测质量依赖 LLM 模型能力，过短文本可能识别不准确。"
    )
    args_schema: type[BaseModel] = DetectLanguageInput

    def _run(self, text: str) -> str:
        logger.info("🌐 语言检测请求")

        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

            model = get_chat_model(temperature=0.0)

            prompt = (
                "请检测以下文本的语言，只返回语言的中文名称（如：中文、英语、日语、法语等），"
                "不要添加任何其他内容。\n\n"
                f"文本：{text[:500]}"
            )

            response = model.invoke([{"role": "user", "content": prompt}])
            result = getattr(response, "content", "").strip()

            logger.info(f"🌐 语言检测结果: {result}")
            return result if result else "无法检测"

        except Exception as e:
            error_msg = f"语言检测失败: {e!s}"
            logger.exception(error_msg)
            return error_msg

    async def _arun(self, text: str) -> str:
        return self._run(text=text)


translate_text = TranslateTextTool()
detect_language = DetectLanguageTool()


def get_translation_tools():
    return [translate_text, detect_language]


TRANSLATION_TOOLS = get_translation_tools()
