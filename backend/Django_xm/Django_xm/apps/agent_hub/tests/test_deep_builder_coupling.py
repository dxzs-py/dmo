"""环 3 破除结构守护（spec eliminate-priority-debts 任务 4.3）。

背景（审计 arch §4.4）：agent_hub ↔ research 曾为双向循环：
- 正向（agent_hub → research）：deep_builder 模块级 import research.prompts
  （DEEP_RESEARCH_SYSTEM_PROMPT / DOC_ANALYSIS_PROMPT_SUFFIX）、
  research.services._constants（SANDBOX_ALLOWED_DIRS）、research.services.adapter
  （OfficialDeepAgentAdapter 类型）；spawn.py 延迟 import research.prompts 与
  RETRIEVER_TOOL_NAME_PREFIXES。
- 反向（research → agent_hub）：patches.py _DeepAgentExecutor 继承 AgentExecutor。

任务 4.3 处置：prompt/常量经 AgentConfig 注入或下沉 common；子代理角色 prompt
上移 ai_engine/subagent_runtime/registry.py；deep_builder 返回 AgentWrapper
元数据载体、adapter 组装上移 research.agent_assembly；执行骨架下沉
common.execution_loop（patches.py 去继承）。本测试守护边不回潮。

覆盖：
1. 环 3 正向边零回潮：agent_hub 生产代码（非 tests）无任何 research import 行；
2. E8 第 4 消费点不复活：agent_assembly.py 源码不出现 builders.deep_builder
   借道引用；agent_hub 包外不存在任何 builders.deep_builder 符号级导入；
3. 反向边不回潮：patches.py 源码无 agent_hub import（执行骨架走 common）；
4. C7 三方私有 API 依赖标注在案：deep_builder 源码保留 deepagents._models
   私有 API 依赖说明，防止后续改动无意删除升级风险提示。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_deep_builder_coupling
"""

import re
import unittest
from pathlib import Path

# Django_xm 包根（tests → agent_hub → apps → Django_xm）
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_AGENT_HUB_DIR = _PACKAGE_ROOT / "apps" / "agent_hub"

# 符号级/模块级 import 行（绝对路径形式）；docstring 内的散文提及不匹配
_DEEP_BUILDER_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+[\w.]*builders\.deep_builder\s+import\s|import\s+[\w.]*builders\.deep_builder\b)"
)
_RESEARCH_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+Django_xm\.apps\.research[\w.]*\s+import\s|import\s+Django_xm\.apps\.research[\w.]*)"
)
_AGENT_HUB_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+Django_xm\.apps\.agent_hub[\w.]*\s+import\s|import\s+Django_xm\.apps\.agent_hub[\w.]*)"
)


class AgentHubNoResearchImportTests(unittest.TestCase):
    """环 3 正向边守护：agent_hub 生产代码零 research import（环 3 破除后不得回潮）。"""

    def test_no_research_import_in_agent_hub_production_code(self):
        offenders: list[str] = []
        for py_file in _AGENT_HUB_DIR.rglob("*.py"):
            if py_file.is_dir() or (py_file.parent.name == "tests" and py_file.name != "__init__.py"):
                continue
            for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
                if _RESEARCH_IMPORT_RE.match(line):
                    offenders.append(f"{py_file.relative_to(_PACKAGE_ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "agent_hub 生产代码出现 research import 行（环 3 正向边回潮）：\n" + "\n".join(offenders),
        )


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


class ReverseEdgeEliminatedTests(unittest.TestCase):
    """环 3 反向边守护：research 执行器不再继承 agent_hub.AgentExecutor（骨架走 common）。"""

    def test_patches_has_no_agent_hub_import(self):
        source = (_PACKAGE_ROOT / "apps" / "research" / "services" / "patches.py").read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if _AGENT_HUB_IMPORT_RE.match(line)
        ]
        self.assertEqual(
            offenders,
            [],
            "research/services/patches.py 出现 agent_hub import 行（环 3 反向边回潮，"
            "执行骨架应消费 Django_xm.common.execution_loop）：\n" + "\n".join(offenders),
        )
        self.assertIn("from Django_xm.common.execution_loop import", source)

    def test_deep_builder_returns_agent_wrapper_not_adapter(self):
        """deep_builder 不再依赖 research 侧 adapter 类型（返回 AgentWrapper 元数据载体）。"""
        source = (_AGENT_HUB_DIR / "builders" / "deep_builder.py").read_text(encoding="utf-8")
        self.assertNotIn("OfficialDeepAgentAdapter(", source, "deep_builder 不得再组装 research adapter")
        self.assertIn("AgentWrapper(", source)


class ThirdPartyPrivateApiAnnotationTests(unittest.TestCase):
    """C7 三方库私有 API 依赖标注守护（deepagents._models.get_model_provider）。"""

    def test_private_api_annotation_present_in_deep_builder(self):
        source = (_AGENT_HUB_DIR / "builders" / "deep_builder.py").read_text(encoding="utf-8")
        self.assertIn("deepagents._models", source)
        self.assertIn("私有 API", source)


if __name__ == "__main__":
    unittest.main()
