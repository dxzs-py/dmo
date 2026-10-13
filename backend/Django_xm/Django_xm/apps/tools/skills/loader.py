"""SkillLoader — Skills 子系统的文件系统加载器

负责 Skill 包的文件系统操作：
- discover_skills(): Level 1 Discovery，扫描数据库返回元数据
- activate_skill(): Level 2 Activation，加载 SKILL.md 指令内容
- load_resource(): Level 3 Execution，按需加载资源文件
- validate_skill_package() / install_skill_package() / uninstall_skill_package(): 包管理

DeepAgent skills 参数适配已移至 SkillAdapter.to_deep_agent_skills()。
"""

import logging
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# SKILL.md name 字段验证正则：小写字母+数字+连字符，1-64字符
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")

# ZIP 解压安全上限（F1，防 ZIP bomb；调整常量即可重新配置）
SKILL_ZIP_MAX_ENTRIES = 2000                  # 压缩包成员数上限
SKILL_ZIP_MAX_FILE_SIZE = 50 * 1024 * 1024    # 单成员解压后体积上限（50MB）
SKILL_ZIP_MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 总解压体积上限（500MB）
SKILL_MD_MAX_SIZE = 1024 * 1024               # SKILL.md 体积上限（validate 阶段防解压内存放大）


class SkillLoader:
    """Agent Skills 规范的渐进式披露加载器"""

    def __init__(self, skills_base_dir: str | None = None):
        """初始化 SkillLoader

        Args:
            skills_base_dir: Skill 包存储根目录，默认为 TOOLS_SKILLS_DIR (DATA_DIR/tools/skills)
        """
        if skills_base_dir is None:
            from django.conf import settings

            skills_base_dir = str(
                getattr(
                    settings,
                    "TOOLS_SKILLS_DIR",
                    getattr(settings, "DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data"))
                    / "tools"
                    / "skills",
                )
            )
        self.skills_base_dir = skills_base_dir
        os.makedirs(self.skills_base_dir, exist_ok=True)

    # ---- Level 1: Discovery ----

    def discover_skills(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """扫描数据库和文件系统，返回 Level 1 元数据列表

        Args:
            user_id: 用户 ID，为 None 时返回所有活跃 Skill

        Returns:
            元数据列表，每项包含 name, description, source, version
        """
        from Django_xm.apps.tools.models import SkillPackage

        qs = SkillPackage.objects.filter(status="active")
        if user_id is not None:
            qs = qs.filter(user_id=user_id) | qs.filter(source="system")

        skills = []
        for pkg in qs:
            skills.append(
                {
                    "name": pkg.name,
                    "description": pkg.description,
                    "source": pkg.source,
                    "version": pkg.version,
                    "skill_dir": pkg.skill_dir,
                    "user_id": pkg.user_id,
                }
            )
        return skills

    # ---- Level 2: Activation ----

    def activate_skill(self, name: str, user_id: int | None = None) -> str | None:
        """加载 SKILL.md 完整指令内容（Level 2 Activation）

        Args:
            name: Skill 名称
            user_id: 用户 ID

        Returns:
            SKILL.md 的 Markdown body 内容（不含 frontmatter），未找到返回 None
        """
        skill_dir = self._get_skill_dir(name, user_id)
        if skill_dir is None:
            return None

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md_path):
            logger.warning(f"Skill '{name}' 的 SKILL.md 不存在: {skill_md_path}")
            return None

        try:
            content = self._read_skill_md(skill_md_path)
            return content  # 返回 body 部分（指令内容）
        except Exception:
            logger.exception(f"加载 Skill '{name}' 指令失败")
            return None

    # ---- Level 3: Execution ----

    def load_resource(self, name: str, resource_path: str, user_id: int | None = None) -> str | None:
        """按需加载 Skill 资源文件（Level 3 Execution）

        Args:
            name: Skill 名称
            resource_path: 相对于 Skill 根目录的文件路径（如 'scripts/extract.py'）
            user_id: 用户 ID

        Returns:
            文件内容（文本），二进制文件返回 base64 编码，未找到返回 None
        """
        skill_dir = self._get_skill_dir(name, user_id)
        if skill_dir is None:
            return None

        full_path = os.path.join(skill_dir, resource_path)
        # 安全检查：防止路径遍历。用 resolve + is_relative_to 统一 helper
        # （Task 10 遗留收尾）：裸 startswith 无分隔符收尾时，同用户技能名
        # 互为前缀（"a"/"a2"）可经 "../a2/x" 读到另一技能的文件
        if not self._is_within_base_dir(full_path, skill_dir):
            logger.warning(f"路径遍历攻击检测: {resource_path}")
            return None

        if not os.path.isfile(full_path):
            logger.warning(f"Skill '{name}' 资源文件不存在: {full_path}")
            return None

        try:
            # 尝试文本读取
            with open(full_path, encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            # 二进制文件返回 base64
            import base64

            with open(full_path, "rb") as f:
                return base64.b64encode(f.read()).decode("ascii")

    # ---- 包管理 ----

    def validate_skill_package(self, zip_path: str) -> tuple[bool, str, dict | None]:
        """验证压缩包是否符合 Agent Skills 规范

        Args:
            zip_path: 压缩包路径

        Returns:
            (is_valid, message, frontmatter_dict)
        """
        if not os.path.isfile(zip_path):
            return False, f"文件不存在: {zip_path}", None

        if not zipfile.is_zipfile(zip_path):
            return False, "不是有效的 ZIP 文件", None

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # 查找 SKILL.md
                skill_md_candidates = [n for n in zf.namelist() if n.endswith("SKILL.md") and "__MACOSX" not in n]

                if not skill_md_candidates:
                    return False, "压缩包中未找到 SKILL.md 文件", None

                skill_md_name = skill_md_candidates[0]
                # SKILL.md 体积上限：validate 阶段就要整读内容，防超大声明体积
                # 的成员在解压上限生效前造成内存放大（F1 配套）
                if zf.getinfo(skill_md_name).file_size > SKILL_MD_MAX_SIZE:
                    return False, f"SKILL.md 体积超过 {SKILL_MD_MAX_SIZE} 字节上限", None
                # 提取 Skill 目录名（SKILL.md 的父目录名）
                parts = skill_md_name.split("/")
                # 过滤空字符串（如 "my-skill/SKILL.md" → ["my-skill", "SKILL.md"]）
                parts = [p for p in parts if p]
                if len(parts) < 2:
                    # SKILL.md 在压缩包根目录
                    dir_name = os.path.splitext(os.path.basename(zip_path))[0]
                else:
                    dir_name = parts[-2]

                # 读取并验证 SKILL.md
                content = zf.read(skill_md_name).decode("utf-8")
                frontmatter, _body = self._parse_frontmatter(content)

                if frontmatter is None:
                    return False, "SKILL.md 缺少 YAML frontmatter", None

                # 验证必填字段
                name = frontmatter.get("name", "")
                description = frontmatter.get("description", "")

                if not name:
                    return False, "SKILL.md frontmatter 缺少必填字段 'name'", None
                if not description:
                    return False, "SKILL.md frontmatter 缺少必填字段 'description'", None

                # 验证 name 格式
                if not SKILL_NAME_PATTERN.match(name):
                    return False, f"name '{name}' 不符合规范：仅允许小写字母、数字和连字符，1-64字符", None

                # 验证 name 与目录名关系（允许目录名带后缀，如 agent-browser-clawdbot）
                if name != dir_name and not dir_name.startswith(name + "-"):
                    return (
                        False,
                        f"name '{name}' 与目录名 '{dir_name}' 不匹配（目录名应为 '{name}' 或以 '{name}-' 开头）",
                        None,
                    )

                # 验证 description 长度
                if len(description) > 1024:
                    return False, f"description 超过 1024 字符限制（当前 {len(description)} 字符）", None

                return True, "验证通过", frontmatter

        except zipfile.BadZipFile:
            return False, "ZIP 文件损坏", None
        except Exception as e:
            return False, f"验证失败: {e}", None

    def install_skill_package(self, zip_path: str, user, source: str = "user") -> tuple[bool, str, dict | None]:
        """安装/更新 Skill 包（C10：先落盘、后写库，失败全量回滚）

        流程：解压到同分区临时目录 → 旧目录改名让位（备份）→ 临时目录
        就位为目标目录 → 写 DB → 清理备份。任一步失败：删除半成品目录、
        恢复旧目录，DB 记录保持原状——不产生 skill_dir 指向已删目录的
        悬挂记录。

        Args:
            zip_path: 压缩包路径
            user: Django User 实例
            source: 来源（system/user）

        Returns:
            (success, message, metadata_dict | None)
        """
        from Django_xm.apps.tools.models import SkillPackage, ToolCategory

        is_valid, msg, frontmatter = self.validate_skill_package(zip_path)
        if not is_valid:
            return False, msg, None
        if frontmatter is None:
            # validate_skill_package 返回 is_valid=True 时 frontmatter 必非 None，此处为防御性检查
            raise ValueError(f"技能包 '{zip_path}' 校验失败: frontmatter 缺失")

        name = frontmatter["name"]
        description = frontmatter.get("description", "")
        version = (
            str(frontmatter.get("metadata", {}).get("version", "1.0.0"))
            if isinstance(frontmatter.get("metadata"), dict)
            else "1.0.0"
        )
        license_str = frontmatter.get("license", "")
        compatibility = frontmatter.get("compatibility", "")
        metadata_json = frontmatter.get("metadata", {})

        existing = SkillPackage.objects.filter(user=user, name=name).first()
        target_dir = os.path.join(self.skills_base_dir, str(user.id), name)
        # 临时/备份目录与目标同分区（仅追加后缀），保证 rename 原子性
        tmp_dir = f"{target_dir}.tmp-{uuid.uuid4().hex[:8]}"
        backup_dir = f"{target_dir}.bak-{uuid.uuid4().hex[:8]}"
        backup_from: str | None = None  # 已挪入 backup_dir 的旧内容来源路径
        placed = False  # 新内容是否已就位 target_dir（决定失败时能否删除）

        try:
            category = ToolCategory.objects.get(code="general")
            self._extract_skill_zip(zip_path, tmp_dir)

            # 旧内容让位：优先认领 DB 记录指向的旧目录；target_dir 上可能
            # 同时存在历史失败残留（无记录认领），一并清掉
            if existing and existing.skill_dir and os.path.isdir(existing.skill_dir):
                if existing.skill_dir != target_dir and os.path.isdir(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                os.rename(existing.skill_dir, backup_dir)
                backup_from = existing.skill_dir
            elif os.path.isdir(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)

            # 新内容就位为目标目录
            os.rename(tmp_dir, target_dir)
            placed = True

            # 目录就绪后再写 DB；失败则回滚目录，DB 记录保持原状
            _pkg, created = SkillPackage.objects.update_or_create(
                user=user,
                name=name,
                defaults={
                    "description": description[:500],
                    "version": version[:20],
                    "license": license_str[:200],
                    "compatibility": compatibility[:500],
                    "metadata_json": metadata_json,
                    "skill_dir": target_dir,
                    "tool_type": "skill",
                    "category": category,
                    "source": source,
                    "status": "active",
                },
            )

            # 提交成功：清理备份
            if backup_from:
                shutil.rmtree(backup_dir, ignore_errors=True)

            action = "安装" if created else "更新"
            logger.info(f"Skill '{name}' {action}成功 (user={user.id}, dir={target_dir})")

            return (
                True,
                f"Skill '{name}' {action}成功",
                {
                    "name": name,
                    "description": description,
                    "version": version,
                    "source": source,
                },
            )

        except Exception as e:
            # 回滚：删除半成品目标目录（仅在新内容已就位时），恢复旧目录
            if placed and os.path.isdir(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            if backup_from and os.path.isdir(backup_dir):
                try:
                    os.rename(backup_dir, backup_from)
                except Exception:
                    logger.exception(f"恢复旧 Skill 目录失败: {backup_dir} -> {backup_from}")
            logger.exception("安装 Skill 失败")
            return False, f"安装失败: {e}", None
        finally:
            # 清理解压残留（成功路径下 tmp_dir 已被 rename 走，此处为 no-op）
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def uninstall_skill_package(self, name: str, user) -> tuple[bool, str]:
        """卸载 Skill 包：删除数据库记录和文件目录

        Args:
            name: Skill 名称
            user: Django User 实例

        Returns:
            (success, message)
        """
        from Django_xm.apps.tools.models import SkillPackage

        pkg = SkillPackage.objects.filter(user=user, name=name).first()
        if pkg is None:
            return False, f"未找到 Skill '{name}'"

        if pkg.source == "system":
            return False, f"系统级 Skill '{name}' 不可删除"

        # 删除文件目录
        self._remove_skill_dir(pkg.skill_dir)

        # 删除数据库记录
        pkg.delete()

        logger.info(f"Skill '{name}' 已卸载 (user={user.id})")
        return True, f"Skill '{name}' 已卸载"

    # ---- 内部方法 ----

    @staticmethod
    def _is_within_base_dir(path: str, base_dir: str) -> bool:
        """路径包含校验：path 解析后必须仍位于 base_dir 内，否则 False。

        统一使用 resolve().is_relative_to 而非裸 normpath+startswith：
        - 裸 startswith 无分隔符收尾时 "…/a" 会误放行 "…/a2"（同名前缀目录互窜）；
        - resolve 规范化 ".." 与符号链接，绝对路径入参不会误判为包含。
        解析失败（OSError/ValueError）一律视为不通过。
        """
        try:
            return Path(path).resolve().is_relative_to(Path(base_dir).resolve())
        except (OSError, ValueError):
            return False

    def _get_skill_dir(self, name: str, user_id: int | None = None) -> str | None:
        """获取 Skill 的文件系统目录路径"""
        from Django_xm.apps.tools.models import SkillPackage

        qs = SkillPackage.objects.filter(name=name, status="active")
        if user_id is not None:
            qs = qs.filter(user_id=user_id) | qs.filter(source="system")
        pkg = qs.first()
        if pkg and pkg.skill_dir and os.path.isdir(pkg.skill_dir):
            return pkg.skill_dir
        return None

    def _read_skill_md(self, path: str) -> str:
        """读取 SKILL.md 并返回 body 部分（指令内容，不含 frontmatter）"""
        with open(path, encoding="utf-8") as f:
            content = f.read()
        _, body = self._parse_frontmatter(content)
        return body or ""

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict | None, str]:
        """解析 SKILL.md 的 YAML frontmatter 和 Markdown body

        Returns:
            (frontmatter_dict, body_text)
        """
        # 匹配 --- ... --- 模式
        pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)
        match = pattern.match(content)
        if not match:
            return None, content

        try:
            frontmatter = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None, content

        body = match.group(2).strip()
        return frontmatter or {}, body

    @staticmethod
    def _extract_skill_zip(zip_path: str, dest_dir: str) -> None:
        """解压 Skill 包到目标目录（C10：先落临时目录，成功后才替换旧目录）

        安全约束（F1）：
        - ZIP bomb 上限：成员数 / 单成员解压体积 / 总解压体积三重校验，
          超限抛错整体失败（zipfile 按 header 声明的 file_size 截断读取，
          上限校验在写盘前完成，不产出半成品）；
        - Zip Slip：每个成员落点 resolve 后必须仍在 dest_dir 内（拦截
          绝对路径成员与 "../" 穿越成员）。选择"任一恶意成员即整体失败"
          而非跳过：安装是原子单元（C10 语义），部分解压的"净化包"会静默
          丢失成员且其余内容照常生效，攻击者可借内容不一致混淆后续执行；
          抛错后由 install_skill_package 既有 except 路径统一回滚清理。

        提取逻辑与旧实现一致：仅提取 SKILL.md 所在前缀内的文件，
        跳过 __MACOSX 与目录项。
        """
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [
                info
                for info in zf.infolist()
                if not info.is_dir() and "__MACOSX" not in info.filename
            ]
            if len(members) > SKILL_ZIP_MAX_ENTRIES:
                raise ValueError(f"ZIP 成员数超限: {len(members)} > {SKILL_ZIP_MAX_ENTRIES}")
            total_size = sum(info.file_size for info in members)
            if total_size > SKILL_ZIP_MAX_TOTAL_SIZE:
                raise ValueError(f"ZIP 总解压体积超限: {total_size} > {SKILL_ZIP_MAX_TOTAL_SIZE}")
            for info in members:
                if info.file_size > SKILL_ZIP_MAX_FILE_SIZE:
                    raise ValueError(
                        f"ZIP 单文件解压体积超限: {info.filename} ({info.file_size} > {SKILL_ZIP_MAX_FILE_SIZE})"
                    )

            prefix = ""
            skill_md_candidates = [n for n in zf.namelist() if n.endswith("SKILL.md") and "__MACOSX" not in n]
            if skill_md_candidates:
                skill_md_name = skill_md_candidates[0]
                parts = skill_md_name.split("/")
                non_empty = [p for p in parts if p]
                if len(non_empty) >= 2:
                    prefix = "/".join(parts[: parts.index(non_empty[0]) + 1]) + "/"

            for info in members:
                member = info.filename
                if prefix and member.startswith(prefix):
                    relative = member[len(prefix) :]
                else:
                    relative = member
                if not relative:
                    continue

                target_path = os.path.join(dest_dir, relative)
                if not SkillLoader._is_within_base_dir(target_path, dest_dir):
                    raise ValueError(f"ZIP 成员路径非法（Zip Slip）: {member}")
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "wb") as f:
                    f.write(zf.read(member))

    @staticmethod
    def _remove_skill_dir(skill_dir: str) -> None:
        """安全删除 Skill 目录"""
        if skill_dir and os.path.isdir(skill_dir):
            try:
                shutil.rmtree(skill_dir)
            except Exception as e:
                logger.warning(f"删除 Skill 目录失败: {skill_dir}, {e}")
