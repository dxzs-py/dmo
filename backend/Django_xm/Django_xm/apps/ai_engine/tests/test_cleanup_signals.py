"""ai_engine 数据清理信号接线测试。

验证 apps.py ready() 导入 signals 后，ai_data_cleanup_needed 信号
能够触发 schedule_ai_data_cleanup（用户注销/删除、会话删除的清理链）。
"""

from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.core.signals import ai_data_cleanup_needed


class AiDataCleanupSignalTests(SimpleTestCase):
    def test_receiver_registered(self):
        """AppConfig.ready() 后 receiver 应已注册"""
        self.assertTrue(
            ai_data_cleanup_needed.receivers,
            "ai_data_cleanup_needed 没有注册任何 receiver",
        )

    def test_signal_dispatches_cleanup(self):
        """发出信号应调用 schedule_ai_data_cleanup 且透传参数"""
        with mock.patch(
            "Django_xm.apps.ai_engine.services.cross_app.schedule_ai_data_cleanup"
        ) as mock_schedule:
            ai_data_cleanup_needed.send(
                sender=self.__class__,
                user_id=42,
                session_id=None,
            )

        mock_schedule.assert_called_once_with(user_id=42, session_id=None)

    def test_session_scoped_cleanup(self):
        """会话删除场景：session_id 透传"""
        with mock.patch(
            "Django_xm.apps.ai_engine.services.cross_app.schedule_ai_data_cleanup"
        ) as mock_schedule:
            ai_data_cleanup_needed.send(
                sender=self.__class__,
                user_id=None,
                session_id="sess-abc",
            )

        mock_schedule.assert_called_once_with(user_id=None, session_id="sess-abc")
