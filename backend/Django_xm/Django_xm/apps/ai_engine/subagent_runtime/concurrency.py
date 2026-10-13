"""子代理进程级并发上限（D3/P2-9：子代理并发 Semaphore 上限）。

现状（审计 PF-03）：spawn/resume 直接 ``threading.Thread`` 启动子代理执行
线程，兄弟实例数无上限——主代理单 turn 理论可 spawn 任意多子代理，每实例
1 线程 + 1 独立事件循环 + 1 个 per-loop PG checkpointer 连接池，FastAPI
进程与 PG 连接池承压。

实现要点：
- 收敛点在 ``langgraph_adapter._run_in_thread``（spawn / 审批恢复 / 等待
  恢复 / 重启恢复全部执行线程的唯一入口），单点获取并发槽，无多入口漏
  覆盖、无重复计数；
- 信号量用 ``threading.BoundedSemaphore`` 而非 ``asyncio.Semaphore``：
  子代理在独立线程 + 独立事件循环执行（``asyncio.new_event_loop``），
  asyncio 原语绑定单一事件循环，跨 loop await 会抛
  "bound to a different event loop"；threading 信号量的等待发生在创建
  事件循环之前的纯线程上下文，天然跨 loop 安全；
- 排队语义：超限时新线程阻塞在槽获取上（不阻塞任何事件循环、不占工具
  超时——spawn 工具立即返回，排队发生在子代理自身线程内、其图尚未启动），
  前序子代理终态释放槽后由 threading 条件变量调度唤醒；
- Bounded 防 over-release：释放次数超过获取次数立即 ValueError，防槽位
  膨胀导致上限悄然失效。

默认值取 8 的依据（与 B6 超时矩阵 / SessionManager 容量协调）：
- 审计 PF-03 建议 4-8，取上界：可同时容纳 1-2 个完整深研 fan-out 波次
  （每波典型 2-5 个子代理）；
- 8 个 per-loop PG checkpointer 连接池（每池 ≥1 连接）≈ 8-16 连接，处于
  PG 默认 max_connections=100 的安全余量内；
- SessionManager 并发会话槽 DEFAULT_MAX_CONCURRENCY=20（chat 会话多数
  0-2 个子代理、research fan-out 每波 2-5 个），8 槽覆盖常态并留余量，
  突发由排队吸收；
- 槽位生命周期 ≤ 单个子代理图执行时长（recursion_limit=200、工具级超时
  300s、LLM SDK 120s 约束），排队延迟为分钟级 ≪ 挂起会话 watchdog
  7200s，不会导致等待父会话被 watchdog 误终态；
- Celery 全局 hard 1800s 属独立 worker 进程域（子代理运行于 FastAPI 执行
  服务进程，不经 Celery），仅作量级参照，无直接耦合。

生产可经 Django settings 覆盖：AGENT_SUBAGENT_CONCURRENCY（最小 1，显式
<1 按 1 处理并告警——并发上限无"禁用"语义，无界即审计缺陷本身）。
代码级默认与 settings/base.py 显式装配同值（双层兜底，同
agent_resilience.DEFAULT_SOFT/HARD_TIMEOUT 模式）。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 子代理进程级并发上限默认值。settings.AGENT_SUBAGENT_CONCURRENCY 可覆盖。
DEFAULT_SUBAGENT_CONCURRENCY: int = 8

# 并发槽排队等待超时默认值（秒）。settings.AGENT_SUBAGENT_ACQUIRE_TIMEOUT 可覆盖；
# <=0 / None 表示无限等待（维持旧排队语义）。取 30s 依据（round3 2.5）：
# 槽位生命周期受 recursion_limit/工具超时约束为分钟级，正常排队在秒级得到
# 调度；超过 30s 仍未获得槽位说明并发面已饱和，继续无限排队会让子代理
# 线程悬挂且父会话 fan-in 等待者一直收不到终态（挂到 watchdog 7200s）。
# 超时后实例置 FAILED 并触发 _notify_finished，父会话立即感知失败。
DEFAULT_SUBAGENT_ACQUIRE_TIMEOUT: float = 30.0


def get_subagent_concurrency_limit() -> int:
    """读取子代理并发上限（settings 覆盖 → 代码默认；非法值防呆回退）。

    Returns:
        并发上限（int，≥1）。settings 未配置/非法（非数值）回退代码默认；
        显式 <1 按 1 处理（串行化）并告警，不提供"无界"出口。
    """
    try:
        from django.conf import settings

        raw = getattr(settings, "AGENT_SUBAGENT_CONCURRENCY", DEFAULT_SUBAGENT_CONCURRENCY)
        limit = int(raw)
    except Exception as e:
        logger.warning(
            f"[SubAgentConcurrency] 读取 AGENT_SUBAGENT_CONCURRENCY 失败，"
            f"回退默认值 {DEFAULT_SUBAGENT_CONCURRENCY}: {e}"
        )
        return DEFAULT_SUBAGENT_CONCURRENCY
    if limit < 1:
        logger.warning(
            f"[SubAgentConcurrency] AGENT_SUBAGENT_CONCURRENCY={limit} 非法（<1），"
            "按 1 处理（并发上限无禁用语义）"
        )
        return 1
    return limit


_semaphore: threading.BoundedSemaphore | None = None
_semaphore_lock = threading.Lock()


def get_subagent_concurrency_acquire_timeout() -> float | None:
    """读取并发槽排队等待超时（settings 覆盖 → 代码默认）。

    Returns:
        超时秒数（>0）；settings 显式 <=0 / None 或非法值时返回 None
        （无限等待，维持旧排队语义——禁用语义须显式声明而非缺省）。
    """
    try:
        from django.conf import settings

        raw = getattr(settings, "AGENT_SUBAGENT_ACQUIRE_TIMEOUT", DEFAULT_SUBAGENT_ACQUIRE_TIMEOUT)
    except Exception as e:
        logger.warning(
            f"[SubAgentConcurrency] 读取 AGENT_SUBAGENT_ACQUIRE_TIMEOUT 失败，"
            f"回退默认值 {DEFAULT_SUBAGENT_ACQUIRE_TIMEOUT}: {e}"
        )
        return DEFAULT_SUBAGENT_ACQUIRE_TIMEOUT
    if raw is None:
        return None
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"[SubAgentConcurrency] AGENT_SUBAGENT_ACQUIRE_TIMEOUT={raw!r} 非法（非数值），按无限等待处理"
        )
        return None
    if timeout <= 0:
        return None
    return timeout


def get_subagent_concurrency_semaphore() -> threading.BoundedSemaphore:
    """获取进程级并发槽信号量（惰性创建，容量在首次使用时定格）。

    惰性初始化：避免 import 期强依赖 Django settings（与
    runtime._get_max_subagent_depth 同源考量）。
    """
    global _semaphore  # noqa: PLW0603 - 进程级单例惰性初始化（标准双检锁模式）
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                limit = get_subagent_concurrency_limit()
                _semaphore = threading.BoundedSemaphore(limit)
                logger.info(f"[SubAgentConcurrency] 子代理并发槽已初始化: limit={limit}")
    return _semaphore


def reset_subagent_concurrency_semaphore() -> None:
    """重置进程级并发槽（测试或运行期配置变更后重建）。

    调用方必须保证无在途持有者：重置丢弃旧信号量的计数状态，在途持有者
    后续 release 落在旧对象上（不污染新信号量），但新信号量容量不再受其在
    途执行约束。生产运行期调用需在全部子代理终态后进行。
    """
    global _semaphore  # noqa: PLW0603 - 进程级单例重置点
    with _semaphore_lock:
        _semaphore = None
