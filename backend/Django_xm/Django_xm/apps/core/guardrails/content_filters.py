"""
内容安全过滤器 - 检测和过滤不安全内容
"""

import re
from enum import Enum
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass


class ContentSafetyLevel(Enum):
    SAFE = "safe"
    WARNING = "warning"
    UNSAFE = "unsafe"


@dataclass
class FilterResult:
    is_safe: bool
    safety_level: ContentSafetyLevel
    issues: List[str]
    filtered_content: str
    details: Dict[str, Any]


class ContentFilter:
    PATTERNS = {
        "phone": r"1[3-9]\d{9}",
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "id_card": r"\d{17}[\dXx]",
        "credit_card": r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}",
        "ip_address": r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
    }

    UNSAFE_KEYWORDS = [
        "暴力", "色情", "赌博", "毒品", "恐怖", "诈骗",
        "hack", "crack", "exploit", "malware", "virus",
    ]

    INJECTION_PATTERNS = [
        r"ignore\s+previous\s+instructions",
        r"ignore\s+all\s+previous",
        r"disregard\s+previous",
        r"forget\s+previous",
        r"you\s+are\s+now",
        r"new\s+instructions",
        r"system\s*:\s*",
        r"assistant\s*:\s*",
        r"\[SYSTEM\]",
        r"\[INST\]",
        r"<\|im_start\|>",
    ]

    def __init__(
        self,
        enable_pii_detection: bool = True,
        enable_content_safety: bool = True,
        enable_injection_detection: bool = True,
        mask_pii: bool = True,
    ):
        self.enable_pii_detection = enable_pii_detection
        self.enable_content_safety = enable_content_safety
        self.enable_injection_detection = enable_injection_detection
        self.mask_pii = mask_pii

    def filter_input(self, text: str) -> FilterResult:
        issues = []
        details = {}
        filtered_text = text
        safety_level = ContentSafetyLevel.SAFE

        if self.enable_injection_detection:
            injection_detected, injection_patterns = self._detect_injection(text)
            if injection_detected:
                issues.append(f"检测到潜在的 Prompt Injection 攻击: {injection_patterns}")
                safety_level = ContentSafetyLevel.UNSAFE

        if self.enable_content_safety:
            keyword_issues = self._check_keywords(text)
            if keyword_issues:
                issues.extend(keyword_issues)
                if safety_level != ContentSafetyLevel.UNSAFE:
                    safety_level = ContentSafetyLevel.WARNING

        if self.enable_pii_detection:
            pii_found = self._detect_pii(text)
            if pii_found:
                details["pii_detected"] = pii_found
                if self.mask_pii:
                    filtered_text = self._mask_pii(text, pii_found)
                    issues.append("检测到个人信息，已进行脱敏处理")

        is_safe = safety_level != ContentSafetyLevel.UNSAFE

        return FilterResult(
            is_safe=is_safe,
            safety_level=safety_level,
            issues=issues,
            filtered_content=filtered_text,
            details=details,
        )

    def filter_output(self, text: str) -> FilterResult:
        issues = []
        details = {}
        safety_level = ContentSafetyLevel.SAFE

        if self.enable_content_safety:
            keyword_issues = self._check_keywords(text)
            if keyword_issues:
                issues.extend(keyword_issues)
                safety_level = ContentSafetyLevel.WARNING

        is_safe = safety_level != ContentSafetyLevel.UNSAFE

        return FilterResult(
            is_safe=is_safe,
            safety_level=safety_level,
            issues=issues,
            filtered_content=text,
            details=details,
        )

    def _detect_injection(self, text: str) -> Tuple[bool, List[str]]:
        detected = []
        text_lower = text.lower()
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                detected.append(pattern)
        return len(detected) > 0, detected

    def _check_keywords(self, text: str) -> List[str]:
        issues = []
        text_lower = text.lower()
        for keyword in self.UNSAFE_KEYWORDS:
            if keyword.lower() in text_lower:
                issues.append(f"检测到不安全关键词: {keyword}")
        return issues

    def _detect_pii(self, text: str) -> Dict[str, List[str]]:
        pii_found = {}
        for pii_type, pattern in self.PATTERNS.items():
            matches = re.findall(pattern, text)
            if matches:
                pii_found[pii_type] = matches
        return pii_found

    def _mask_pii(self, text: str, pii_found: Dict[str, List[str]]) -> str:
        masked_text = text
        for pii_type, values in pii_found.items():
            for value in values:
                if pii_type == "phone":
                    masked = value[:3] + "****" + value[-4:]
                elif pii_type == "email":
                    parts = value.split("@")
                    masked = parts[0][:2] + "***@" + parts[1]
                elif pii_type == "id_card":
                    masked = value[:6] + "********" + value[-4:]
                elif pii_type == "credit_card":
                    masked = "****-****-****-" + value[-4:]
                elif pii_type == "ip_address":
                    masked = "*. *. *." + value.split(".")[-1]
                else:
                    masked = "***"
                masked_text = masked_text.replace(value, masked)
        return masked_text