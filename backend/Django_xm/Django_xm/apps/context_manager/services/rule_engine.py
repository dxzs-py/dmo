"""上下文规则引擎

4 层优先级：organization → user_global → project → local
路径作用域规则：通过 path_patterns（glob 模式）实现按需加载
"""

from fnmatch import fnmatch

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.context_manager.models import ContextRule

logger = get_logger(__name__)

# scope 优先级映射：数值越小优先级越高
SCOPE_PRIORITY = {
    "organization": 0,
    "user_global": 1,
    "project": 2,
    "local": 3,
}


class ContextRuleEngine:
    """上下文规则引擎

    4 层优先级：organization → user_global → project → local
    路径作用域规则：通过 path_patterns（glob 模式）实现按需加载
    """

    def __init__(self):
        self._cache: dict = {}  # {user_id: {cache_key: rules}}

    def load_rules(self, user_id, project_id=None, file_paths=None) -> list:
        """加载生效的规则列表

        1. 加载无 path_patterns 的规则（全局生效）
        2. 如果提供了 file_paths，加载 path_patterns 匹配的规则
        3. 按 scope 优先级 + priority 排序
        """
        cache_key = self._build_cache_key(user_id, project_id, file_paths)

        # 检查缓存
        user_cache = self._cache.get(user_id, {})
        if cache_key in user_cache:
            return user_cache[cache_key]

        try:
            rules = self._query_rules(user_id, project_id, file_paths)
        except Exception:
            logger.exception("加载上下文规则失败, user_id=%s", user_id)
            return []

        # 排序：先按 scope 优先级升序，再按 priority 降序
        rules.sort(key=lambda r: (SCOPE_PRIORITY.get(r.scope, 99), -r.priority))

        # 写入缓存
        if user_id not in self._cache:
            self._cache[user_id] = {}
        self._cache[user_id][cache_key] = rules

        return rules

    def get_active_rules(self, user_id, project_id=None) -> list:
        """获取当前活跃规则（仅无 path_patterns 的）"""
        try:
            queryset = ContextRule.objects.filter(
                user_id=user_id,
                is_active=True,
                path_patterns__len=0,
            )
            if project_id:
                queryset = queryset.filter(
                    project_id__in=["", project_id],
                )
            else:
                queryset = queryset.filter(project_id="")

            rules = list(queryset)
        except Exception:
            logger.exception("获取活跃规则失败, user_id=%s", user_id)
            return []

        rules.sort(key=lambda r: (SCOPE_PRIORITY.get(r.scope, 99), -r.priority))
        return rules

    def invalidate_cache(self, user_id=None):
        """清除规则缓存"""
        if user_id is not None:
            self._cache.pop(user_id, None)
        else:
            self._cache.clear()

    def _query_rules(self, user_id, project_id, file_paths) -> list:
        """从数据库查询规则并按路径过滤"""
        queryset = ContextRule.objects.filter(
            user_id=user_id,
            is_active=True,
        )

        if project_id:
            queryset = queryset.filter(
                project_id__in=["", project_id],
            )
        else:
            queryset = queryset.filter(project_id="")

        rules = list(queryset)

        # 分离全局规则和路径作用域规则
        global_rules = [r for r in rules if not r.path_patterns]
        path_scoped_rules = [r for r in rules if r.path_patterns]

        # 全局规则始终生效
        result = list(global_rules)

        # 路径作用域规则：仅当 file_paths 匹配时加载
        if file_paths:
            for rule in path_scoped_rules:
                if self._match_path_patterns(rule.path_patterns, file_paths):
                    result.append(rule)

        return result

    def _match_path_patterns(self, patterns, file_paths) -> bool:
        """检查文件路径是否匹配 glob 模式

        任一 file_path 匹配任一 pattern 即返回 True
        """
        for file_path in file_paths:
            for pattern in patterns:
                if fnmatch(file_path, pattern):
                    return True
        return False

    @staticmethod
    def _build_cache_key(user_id, project_id, file_paths) -> str:
        """构建缓存 key"""
        return f"{user_id}:{project_id or ''}:{','.join(sorted(file_paths or []))}"
