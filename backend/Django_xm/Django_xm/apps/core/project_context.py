"""
项目上下文检测
参考 claw-code-main 的 prompt.rs 中项目上下文构建逻辑
自动检测项目结构和上下文信息
"""

import os
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, field

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

MAX_INSTRUCTION_CHARS = 4000
MAX_TOTAL_INSTRUCTION_CHARS = 12000

INSTRUCTION_FILES = [
    "CLAUDE.md",
    "AI.md",
    "PROJECT.md",
    ".ai-instructions",
    "INSTRUCTIONS.md",
]

PROJECT_MARKERS = {
    "python": ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile", "manage.py"],
    "javascript": ["package.json", "tsconfig.json", "yarn.lock", "pnpm-lock.yaml"],
    "rust": ["Cargo.toml", "Cargo.lock"],
    "go": ["go.mod", "go.sum"],
    "java": ["pom.xml", "build.gradle", "gradlew"],
    "django": ["manage.py", "wsgi.py", "asgi.py"],
    "vue": ["vue.config.js", "vite.config.js", "nuxt.config.js"],
    "react": ["next.config.js", "craco.config.js"],
}

IMPORTANT_DIRS = ["src", "lib", "app", "apps", "components", "pages", "api", "models", "views"]


@dataclass
class ProjectContext:
    project_name: str = ""
    project_root: str = ""
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    has_git: bool = False
    git_branch: str = ""
    instruction_content: str = ""
    directory_structure: str = ""
    key_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "projectName": self.project_name,
            "projectRoot": self.project_root,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "hasGit": self.has_git,
            "gitBranch": self.git_branch,
            "instructionContent": self.instruction_content[:500] if self.instruction_content else "",
            "directoryStructure": self.directory_structure[:1000] if self.directory_structure else "",
            "keyFiles": self.key_files[:20],
        }

    def to_system_prompt_section(self) -> str:
        if not self.project_name and not self.languages:
            return ""

        parts = ["\n## 项目上下文\n"]
        parts.append(f"- 项目名称: {self.project_name}")
        if self.languages:
            parts.append(f"- 编程语言: {', '.join(self.languages)}")
        if self.frameworks:
            parts.append(f"- 框架: {', '.join(self.frameworks)}")
        if self.has_git:
            parts.append(f"- Git 分支: {self.git_branch}")

        if self.instruction_content:
            truncated = self.instruction_content[:MAX_INSTRUCTION_CHARS]
            if len(self.instruction_content) > MAX_INSTRUCTION_CHARS:
                truncated += "\n...(内容过长已截断)"
            parts.append(f"\n### 项目指令\n{truncated}")

        return "\n".join(parts)


class ProjectContextDetector:
    """项目上下文检测器"""

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root) if project_root else None

    def detect(self, search_path: Optional[str] = None) -> ProjectContext:
        root = Path(search_path) if search_path else self.project_root
        if not root or not root.exists():
            return ProjectContext()

        context = ProjectContext(project_root=str(root))
        context.project_name = root.name
        context.has_git = (root / ".git").exists()

        if context.has_git:
            context.git_branch = self._get_git_branch(root)

        self._detect_languages_and_frameworks(root, context)
        context.instruction_content = self._load_instructions(root)
        context.directory_structure = self._build_directory_structure(root)
        context.key_files = self._find_key_files(root)

        return context

    @staticmethod
    def _get_git_branch(root: Path) -> str:
        head_file = root / ".git" / "HEAD"
        if head_file.exists():
            try:
                content = head_file.read_text().strip()
                if content.startswith("ref: refs/heads/"):
                    return content.replace("ref: refs/heads/", "")
            except IOError:
                pass
        return "unknown"

    @staticmethod
    def _detect_languages_and_frameworks(root: Path, context: ProjectContext):
        languages = set()
        frameworks = set()

        for lang, markers in PROJECT_MARKERS.items():
            for marker in markers:
                if (root / marker).exists():
                    if lang in ("django", "vue", "react"):
                        frameworks.add(lang)
                    else:
                        languages.add(lang)
                    break

        if (root / "requirements.txt").exists() or (root / "manage.py").exists():
            languages.add("python")
        if (root / "package.json").exists():
            languages.add("javascript")

        if (root / "pyproject.toml").exists():
            languages.add("python")
            try:
                content = (root / "pyproject.toml").read_text(encoding='utf-8')
                if "django" in content.lower():
                    frameworks.add("django")
                if "fastapi" in content.lower():
                    frameworks.add("fastapi")
            except IOError:
                pass

        if (root / "package.json").exists():
            try:
                content = (root / "package.json").read_text(encoding='utf-8')
                if '"vue"' in content:
                    frameworks.add("vue")
                if '"react"' in content:
                    frameworks.add("react")
                if '"next"' in content:
                    frameworks.add("next.js")
                if '"nuxt"' in content:
                    frameworks.add("nuxt")
            except IOError:
                pass

        context.languages = sorted(languages)
        context.frameworks = sorted(frameworks)

    @staticmethod
    def _load_instructions(root: Path) -> str:
        instructions = []
        total_chars = 0

        for filename in INSTRUCTION_FILES:
            filepath = root / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding='utf-8').strip()
                    if content:
                        if total_chars + len(content) > MAX_TOTAL_INSTRUCTION_CHARS:
                            remaining = MAX_TOTAL_INSTRUCTION_CHARS - total_chars
                            if remaining > 100:
                                instructions.append(f"### {filename}\n{content[:remaining]}")
                            break
                        instructions.append(f"### {filename}\n{content}")
                        total_chars += len(content)
                except IOError:
                    pass

        for subdir in root.iterdir():
            if subdir.is_dir() and not subdir.name.startswith('.'):
                for filename in INSTRUCTION_FILES:
                    filepath = subdir / filename
                    if filepath.exists():
                        try:
                            content = filepath.read_text(encoding='utf-8').strip()
                            if content:
                                if total_chars + len(content) > MAX_TOTAL_INSTRUCTION_CHARS:
                                    break
                                instructions.append(f"### {subdir.name}/{filename}\n{content}")
                                total_chars += len(content)
                        except IOError:
                            pass

        return "\n\n".join(instructions)

    @staticmethod
    def _build_directory_structure(root: Path, max_depth: int = 2) -> str:
        lines = []
        ignore_dirs = {
            '.git', 'node_modules', '__pycache__', '.venv', 'venv',
            'dist', 'build', '.next', '.nuxt', 'target', '.idea',
            '.vscode', 'env', '.env', 'static', 'media',
        }
        ignore_files = {
            '.DS_Store', 'Thumbs.db', '*.pyc', '*.pyo',
        }

        def _walk(path: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return

            dirs = []
            files = []
            for entry in entries:
                if entry.name.startswith('.') and entry.name not in ('.env.example',):
                    continue
                if entry.is_dir():
                    if entry.name not in ignore_dirs:
                        dirs.append(entry)
                else:
                    if entry.name not in ignore_files:
                        files.append(entry)

            for d in dirs[:10]:
                lines.append(f"{prefix}📁 {d.name}/")
                _walk(d, prefix + "  ", depth + 1)

            for f in files[:15]:
                lines.append(f"{prefix}📄 {f.name}")

        lines.append(f"📁 {root.name}/")
        _walk(root, "  ")

        return "\n".join(lines[:100])

    @staticmethod
    def _find_key_files(root: Path) -> List[str]:
        key_filenames = {
            "README.md", "README.rst", "README.txt",
            "requirements.txt", "setup.py", "pyproject.toml",
            "package.json", "Cargo.toml", "go.mod",
            "Dockerfile", "docker-compose.yml",
            ".env.example", "Makefile",
            "manage.py", "wsgi.py", "asgi.py",
            "vite.config.js", "vue.config.js",
        }

        found = []
        for entry in root.iterdir():
            if entry.name in key_filenames:
                found.append(entry.name)

        return sorted(found)


def detect_project_context(search_path: Optional[str] = None) -> ProjectContext:
    detector = ProjectContextDetector()
    return detector.detect(search_path)
