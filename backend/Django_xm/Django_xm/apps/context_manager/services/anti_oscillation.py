"""防震荡保护

压缩后立即回填导致 token 占比反弹的场景：
- 冷却期：压缩成功后 N 步内不再压缩
- 失败计数：压缩后 token 占比仍 > 80% 记为失败
- 自动禁用：连续 N 次失败后禁用自动压缩
"""


class AntiOscillationGuard:
    """防震荡保护

    压缩后立即回填导致 token 占比反弹的场景：
    - 冷却期：压缩成功后 N 步内不再压缩
    - 失败计数：压缩后 token 占比仍 > 80% 记为失败
    - 自动禁用：连续 N 次失败后禁用自动压缩
    """

    def __init__(self, min_cooldown: int = 3, max_failures: int = 3):
        self._cooldown_steps: int = 0
        self._min_cooldown: int = min_cooldown
        self._failure_count: int = 0
        self._max_failures: int = max_failures
        self._disabled: bool = False

    def should_skip(self) -> bool:
        """是否应跳过本次压缩"""
        if self._disabled:
            return True
        if self._cooldown_steps > 0:
            self._cooldown_steps -= 1
            return True
        return False

    def record_success(self):
        """压缩成功，重置失败计数，启动冷却期"""
        self._failure_count = 0
        self._cooldown_steps = self._min_cooldown

    def record_failure(self, post_compress_ratio: float):
        """压缩后若立即超过 80% 阈值则记录失败"""
        if post_compress_ratio > 0.8:
            self._failure_count += 1
            if self._failure_count >= self._max_failures:
                self._disabled = True

    def reset(self):
        """重置所有状态（新会话时调用）"""
        self._cooldown_steps = 0
        self._failure_count = 0
        self._disabled = False

    @property
    def is_disabled(self) -> bool:
        return self._disabled

    @property
    def cooldown_remaining(self) -> int:
        return self._cooldown_steps

    @property
    def failure_count(self) -> int:
        return self._failure_count
