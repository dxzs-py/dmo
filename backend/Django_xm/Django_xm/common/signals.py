"""跨 app Django Signal 定义

所有跨 app 解耦的信号集中定义于此，各 app 监听自己关心的信号，
避免 app 间的直接导入和函数调用耦合。
"""

import django.dispatch

# 审批恢复信号：审批被用户 approve/reject 后发送
# 各 app（chat/learning/research）可监听此信号，执行各自的恢复逻辑
# sender: Approval 模型实例
# 提供参数：interrupt_id, approved, resume_value, source, source_id, user
approval_resumed = django.dispatch.Signal()
