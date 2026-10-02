"""文件类工具审批策略补全单元测试（安全修复 S-03/S-06/S-09）。

覆盖：
1. FsReadFileApprovalPolicy：相对路径 SAFE 自动通过、绝对路径 CONTROLLED 需审批；
2. AttachmentReaderApprovalPolicy：只读附件，固定 SAFE 自动通过；
3. fs_write_file 既有策略回归（绝对 HIGH / 相对 CONTROLLED）；
4. ApprovalMiddleware 默认注册表包含 fs_read_file / fs_write_file /
   attachment_reader / file_reader —— 三个此前"无策略直接执行"的工具
   现已纳入审批链路。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_file_approval_policies
"""

import unittest

from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
from Django_xm.apps.agent_hub.approval.policies import (
    AttachmentReaderApprovalPolicy,
    FsReadFileApprovalPolicy,
    FsWriteFileApprovalPolicy,
)
from Django_xm.common.risk_levels import RiskLevel


class TestFsReadFileApprovalPolicy(unittest.TestCase):
    """fs_read_file 策略分级（口径对齐 FileReaderApprovalPolicy）。"""

    def setUp(self):
        self.policy = FsReadFileApprovalPolicy()

    def test_tool_name(self):
        self.assertEqual(self.policy.tool_name, "fs_read_file")

    def test_relative_path_safe_auto_pass(self):
        """会话工作区内相对路径读取自动通过，不打断正常业务。"""
        args = {"relative_path": "notes/report.md", "thread_id": "sess-1"}
        self.assertEqual(self.policy.assess_risk(args), RiskLevel.SAFE)
        self.assertFalse(self.policy.should_approve(args))

    def test_absolute_path_controlled_requires_approval(self):
        """绝对路径（DATA_DIR 内跨会话读取风险）需审批。"""
        args = {"relative_path": "D:\\data\\chat\\other\\a.md", "thread_id": "sess-1"}
        self.assertEqual(self.policy.assess_risk(args), RiskLevel.CONTROLLED)
        self.assertTrue(self.policy.should_approve(args))

    def test_empty_path_safe(self):
        """空路径交由工具层处理，策略层不拦截。"""
        self.assertEqual(self.policy.assess_risk({}), RiskLevel.SAFE)

    def test_readonly_no_subagent_weighting(self):
        """只读工具在子 agent 中不上调风险等级。"""
        args = {"relative_path": "D:\\data\\x.md", "thread_id": "t"}
        result = self.policy.assess_risk(args, subagent_context={"risk_ceiling": None, "depth": 1})
        self.assertEqual(result, RiskLevel.CONTROLLED)


class TestAttachmentReaderApprovalPolicy(unittest.TestCase):
    """attachment_reader 策略（SAFE 自动通过，属主校验在工具层）。"""

    def setUp(self):
        self.policy = AttachmentReaderApprovalPolicy()

    def test_tool_name(self):
        self.assertEqual(self.policy.tool_name, "attachment_reader")

    def test_always_safe_auto_pass(self):
        args = {"attachment_id": 123}
        self.assertEqual(self.policy.assess_risk(args), RiskLevel.SAFE)
        self.assertFalse(self.policy.should_approve(args))

    def test_danger_level_low(self):
        self.assertEqual(self.policy.assess_danger({"attachment_id": 1}), "low")


class TestFsWriteFilePolicyRegression(unittest.TestCase):
    """fs_write_file 既有策略回归：写操作保持审批（防补全时误降级）。"""

    def setUp(self):
        self.policy = FsWriteFileApprovalPolicy()

    def test_relative_path_controlled(self):
        args = {"relative_path": "notes/a.md", "content": "x", "thread_id": "t"}
        self.assertEqual(self.policy.assess_risk(args), RiskLevel.CONTROLLED)
        self.assertTrue(self.policy.should_approve(args))

    def test_absolute_path_high(self):
        args = {"relative_path": "D:\\docs\\report.md", "content": "x", "thread_id": "t"}
        self.assertEqual(self.policy.assess_risk(args), RiskLevel.HIGH)


class TestDefaultPolicyRegistry(unittest.TestCase):
    """middleware 默认注册表登记断言：三个文件工具不再走"无策略直接执行"。"""

    def setUp(self):
        self.registry = {p.tool_name: p for p in ApprovalMiddleware._default_policies()}

    def test_fs_read_file_registered(self):
        self.assertIn("fs_read_file", self.registry)
        self.assertIsInstance(self.registry["fs_read_file"], FsReadFileApprovalPolicy)

    def test_fs_write_file_registered(self):
        self.assertIn("fs_write_file", self.registry)
        self.assertIsInstance(self.registry["fs_write_file"], FsWriteFileApprovalPolicy)

    def test_attachment_reader_registered(self):
        self.assertIn("attachment_reader", self.registry)
        self.assertIsInstance(self.registry["attachment_reader"], AttachmentReaderApprovalPolicy)

    def test_file_reader_registered(self):
        self.assertIn("file_reader", self.registry)


if __name__ == "__main__":
    unittest.main()
