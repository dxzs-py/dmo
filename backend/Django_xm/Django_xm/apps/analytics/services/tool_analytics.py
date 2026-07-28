import logging
import threading
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolUsageRecord(BaseModel):
    tool_name: str
    timestamp: datetime
    success: bool
    duration_ms: float
    error_code: str | None = None
    user_id: int | None = None


class ToolAnalyticsService:
    _instance: Optional["ToolAnalyticsService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._records: list[ToolUsageRecord] = []
                cls._instance._max_records = 10000
        return cls._instance

    def record(self, record: ToolUsageRecord) -> None:
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records = self._records[-(self._max_records // 2):]

    def get_tool_stats(self, tool_name: str = "") -> dict:
        with self._lock:
            records = self._records
            if tool_name:
                records = [r for r in records if r.tool_name == tool_name]

        if not records:
            return {
                "total_calls": 0,
                "success_count": 0,
                "error_count": 0,
                "error_rate": 0.0,
                "avg_duration_ms": 0.0,
            }

        total = len(records)
        success_count = sum(1 for r in records if r.success)
        error_count = total - success_count
        durations = [r.duration_ms for r in records]
        avg_duration = sum(durations) / total if total > 0 else 0.0

        error_codes: dict[str, int] = {}
        for r in records:
            if r.error_code:
                error_codes[r.error_code] = error_codes.get(r.error_code, 0) + 1

        return {
            "total_calls": total,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate": round(error_count / total, 4) if total > 0 else 0.0,
            "avg_duration_ms": round(avg_duration, 2),
            "error_codes": error_codes,
        }

    def get_top_tools(self, limit: int = 10) -> list[dict]:
        with self._lock:
            records = list(self._records)

        call_counts: dict[str, int] = {}
        for r in records:
            call_counts[r.tool_name] = call_counts.get(r.tool_name, 0) + 1

        sorted_tools = sorted(call_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

        result = []
        for name, count in sorted_tools:
            tool_records = [r for r in records if r.tool_name == name]
            success = sum(1 for r in tool_records if r.success)
            avg_dur = sum(r.duration_ms for r in tool_records) / len(tool_records) if tool_records else 0.0
            result.append({
                "tool_name": name,
                "call_count": count,
                "success_count": success,
                "error_count": count - success,
                "avg_duration_ms": round(avg_dur, 2),
            })

        return result

    def get_error_rate(self, tool_name: str = "") -> float:
        stats = self.get_tool_stats(tool_name=tool_name)
        return stats.get("error_rate", 0.0)

    def get_avg_duration(self, tool_name: str = "") -> float:
        stats = self.get_tool_stats(tool_name=tool_name)
        return stats.get("avg_duration_ms", 0.0)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


def get_tool_analytics_service() -> ToolAnalyticsService:
    return ToolAnalyticsService()
