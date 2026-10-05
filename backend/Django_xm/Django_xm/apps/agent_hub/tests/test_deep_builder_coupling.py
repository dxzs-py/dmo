"""DeepAgentBuilder 隐藏耦合收编结构守护（spec remediate-agent-audit-findings E8）。

背景（审计 arch §1.2-4 / §1.3 C1-C8）：deep_builder 曾是第 4 个外部消费方的
"借道 re-export 常量源"——research 侧 agent_assembly 经 deep_builder 取
DEEP_RESEARCH_SYSTEM_PROMPT（deep_builder 改为函数内延迟导入时该处会静默炸
ImportError）。E8 已正规化为直接从权威定义处 apps/research/prompts.py 导入。

覆盖：
1. 常量同源（is 断言）：deep_builder 持有的 prompt/沙箱常量与权威定义处为
   同一对象——历史上 _SANDBOX_ALLOWED_DIRS 曾在两处定义不一致（漂移防护）；
2. 第 4 消费点不复活：agent_assembly.py 源码不再出现 builders.deep_builder
   借道引用；agent_hub 包外不存在任何 builders.deep_builder 符号级导入
   （factory.py 的模块级注册导入在包内，不受限）；
3. C7 三方私有 API 依赖标注在案：deep_builder 源码保留 deepagents._models
   私有 API 依赖说明，防止后续改动无意删除升级风险提示。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_deep_builder_coupling
"""

import re
import unittest
from pathlib import Path

from Django_xm.apps.agent_hub.builders import deep_builder
from Django_xm.apps.research.prompts import (
    DEEP_RESEARCH_SYSTEM_PROMPT,
    DOC_ANALYSIS_PROMPT_SUFFIX,
)
from Django_xm.apps.research.services import _constants as research_constants

# Django_xm 包根（tests → agent_hub → apps → Django_xm）
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_AGENT_HUB_DIR = _PACKAGE_ROOT / "apps" / "agent_hub"

# 符号级/模块级 import 行（绝对路径形式）；docstring 内的散文提及不匹配
_DEEP_BUILDER_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+[\w.]*builders\.deep_builder\s+import\s|import\s+[\w.]*builders\.deep_builder\b)"
)


class PromptConstantsSingleSourceTests(unittest.TestCase):
    """C1/C2 权威源同对象断言（防再次出现两处定义漂移）。"""

    def test_deep_builder_prompt_constants_are_authoritative_objects(self):
        from Django_xm.apps.research.prompts import DEEP_RESEARCH_SYSTEM_PROMPT as _auth_prompt
        from Django_xm.apps.research.prompts import DOC_ANALYSIS_PROMPT_SUFFIX as _auth_suffix

        self.assertIs(deep_builder.DEEP_RESEARCH_SYSTEM_PROMPT, _auth_prompt)
        self.assertIs(deep_builder.DEEP_RESEARCH_SYSTEM_PROMPT, DEEP_RESEARCH_SYSTEM_PROMPT)
        self.assertIs(deep_builder.DOC_ANALYSIS_PROMPT_SUFFIX, _auth_suffix)
        self.assertIs(deep_builder.DOC_ANALYSIS_PROMPT_SUFFIX, DOC_ANALYSIS_PROMPT_SUFFIX)

    def test_sandbox_allowed_dirs_alias_is_authoritative_object(self):
        self.assertIs(deep_builder._SANDBOX_ALLOWED_DIRS, research_constants.SANDBOX_ALLOWED_DIRS)


class FourthConsumerPointNormalizedTests(unittest.TestCase):
    """第 4 消费点正规化守护：research 侧不再借道 deep_builder 取 prompt 常量。"""

    def test_agent_assembly_has_no_deep_builder_borrow(self):
        source = (_PACKAGE_ROOT / "apps" / "research" / "services" / "agent_assembly.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            "builders.deep_builder",
            source,
            "agent_assembly.py 不得借道 deep_builder（E8 已正规化为直接 import "
            "Django_xm.apps.research.prompts，勿回退）",
        )
        self.assertIn(
            "from Django_xm.apps.research.prompts import DEEP_RESEARCH_SYSTEM_PROMPT",
            source,
        )

    def test_no_symbol_import_of_deep_builder_outside_agent_hub(self):
        """agent_hub 包外禁止 builders.deep_builder 的 import 行（含相对形式缺失防护）。"""
        offenders: list[str] = []
        for py_file in _PACKAGE_ROOT.rglob("*.py"):
            if _AGENT_HUB_DIR in py_file.parents or py_file == _AGENT_HUB_DIR:
                continue
            for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
                if _DEEP_BUILDER_IMPORT_RE.match(line):
                    offenders.append(f"{py_file.relative_to(_PACKAGE_ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "agent_hub 包外出现 deep_builder import 行（第 4 消费点借道回潮）：\n"
            + "\n".join(offenders),
        )


class ThirdPartyPrivateApiAnnotationTests(unittest.TestCase):
    """C7 三方库私有 API 依赖标注守护（deepagents._models.get_model_provider）。"""

    def test_private_api_annotation_present_in_deep_builder(self):
        source = (_AGENT_HUB_DIR / "builders" / "deep_builder.py").read_text(encoding="utf-8")
        self.assertIn("deepagents._models", source)
        self.assertIn("私有 API", source)


if __name__ == "__main__":
    unittest.main()
