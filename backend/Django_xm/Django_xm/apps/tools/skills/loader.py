"""SkillLoader — Skills 子系统的文件系统加载器

负责 Skill 包的文件系统操作：
- discover_skills(): Level 1 Discovery，扫描数据库返回元数据
- activate_skill(): Level 2 Activation，加载 SKILL.md 指令内容
- load_resource(): Level 3 Execution，按需加载资源文件
- validate_skill_package() / install_skill_package() / uninstall_skill_package(): 包管理

DeepAgent skills 参数适配已移至 SkillAdapter.to_deep_agent_skills()。
"""

import os
import re
import shutil
import zipfile
import logging
from typing import Optional, List, Dict, Any, Tuple

import yaml

logger = logging.getLogger(__name__)

# SKILL.md name 字段验证正则：小写字母+数字+连字符，1-64字符
SKILL_NAME_PATTERN = re.compile(r'^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$')


class SkillLoader:
    """Agent Skills 规范的渐进式披露加载器"""

    def __init__(self, skills_base_dir: Optional[str] = None):
        """初始化 SkillLoader

        Args:
            skills_base_dir: Skill 包存储根目录，默认为 TOOLS_SKILLS_DIR (DATA_DIR/tools/skills)
        """
        if skills_base_dir is None:
            from django.conf import settings
            skills_base_dir = str(getattr(settings, 'TOOLS_SKILLS_DIR',
                                          getattr(settings, 'DATA_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'data')) / 'tools' / 'skills'))
        self.skills_base_dir = skills_base_dir
        os.makedirs(self.skills_base_dir, exist_ok=True)

    # ---- Level 1: Discovery ----

    def discover_skills(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """扫描数据库和文件系统，返回 Level 1 元数据列表

        Args:
            user_id: 用户 ID，为 None 时返回所有活跃 Skill

        Returns:
            元数据列表，每项包含 name, description, source, version
        """
        from Django_xm.apps.tools.models import SkillPackage
        qs = SkillPackage.objects.filter(status='active')
        if user_id is not None:
            qs = qs.filter(user_id=user_id) | qs.filter(source='system')

        skills = []
        for pkg in qs:
            skills.append({
                'name': pkg.name,
                'description': pkg.description,
                'source': pkg.source,
                'version': pkg.version,
                'skill_dir': pkg.skill_dir,
                'user_id': pkg.user_id,
            })
        return skills

    # ---- Level 2: Activation ----

    def activate_skill(self, name: str, user_id: Optional[int] = None) -> Optional[str]:
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

        skill_md_path = os.path.join(skill_dir, 'SKILL.md')
        if not os.path.isfile(skill_md_path):
            logger.warning(f"Skill '{name}' 的 SKILL.md 不存在: {skill_md_path}")
            return None

        try:
            content = self._read_skill_md(skill_md_path)
            return content  # 返回 body 部分（指令内容）
        except Exception as e:
            logger.error(f"加载 Skill '{name}' 指令失败: {e}")
            return None

    # ---- Level 3: Execution ----

    def load_resource(self, name: str, resource_path: str, user_id: Optional[int] = None) -> Optional[str]:
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

        full_path = os.path.normpath(os.path.join(skill_dir, resource_path))
        # 安全检查：防止路径遍历
        if not full_path.startswith(os.path.normpath(skill_dir)):
            logger.warning(f"路径遍历攻击检测: {resource_path}")
            return None

        if not os.path.isfile(full_path):
            logger.warning(f"Skill '{name}' 资源文件不存在: {full_path}")
            return None

        try:
            # 尝试文本读取
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # 二进制文件返回 base64
            import base64
            with open(full_path, 'rb') as f:
                return base64.b64encode(f.read()).decode('ascii')

    # ---- 包管理 ----

    def validate_skill_package(self, zip_path: str) -> Tuple[bool, str, Optional[Dict]]:
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
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # 查找 SKILL.md
                skill_md_candidates = [n for n in zf.namelist() if n.endswith('SKILL.md') and '__MACOSX' not in n]

                if not skill_md_candidates:
                    return False, "压缩包中未找到 SKILL.md 文件", None

                skill_md_name = skill_md_candidates[0]
                # 提取 Skill 目录名（SKILL.md 的父目录名）
                parts = skill_md_name.split('/')
                # 过滤空字符串（如 "my-skill/SKILL.md" → ["my-skill", "SKILL.md"]）
                parts = [p for p in parts if p]
                if len(parts) < 2:
                    # SKILL.md 在压缩包根目录
                    dir_name = os.path.splitext(os.path.basename(zip_path))[0]
                else:
                    dir_name = parts[-2]

                # 读取并验证 SKILL.md
                content = zf.read(skill_md_name).decode('utf-8')
                frontmatter, body = self._parse_frontmatter(content)

                if frontmatter is None:
                    return False, "SKILL.md 缺少 YAML frontmatter", None

                # 验证必填字段
                name = frontmatter.get('name', '')
                description = frontmatter.get('description', '')

                if not name:
                    return False, "SKILL.md frontmatter 缺少必填字段 'name'", None
                if not description:
                    return False, "SKILL.md frontmatter 缺少必填字段 'description'", None

                # 验证 name 格式
                if not SKILL_NAME_PATTERN.match(name):
                    return False, f"name '{name}' 不符合规范：仅允许小写字母、数字和连字符，1-64字符", None

                # 验证 name 与目录名关系（允许目录名带后缀，如 agent-browser-clawdbot）
                if name != dir_name and not dir_name.startswith(name + '-'):
                    return False, f"name '{name}' 与目录名 '{dir_name}' 不匹配（目录名应为 '{name}' 或以 '{name}-' 开头）", None

                # 验证 description 长度
                if len(description) > 1024:
                    return False, f"description 超过 1024 字符限制（当前 {len(description)} 字符）", None

                return True, "验证通过", frontmatter

        except zipfile.BadZipFile:
            return False, "ZIP 文件损坏", None
        except Exception as e:
            return False, f"验证失败: {e}", None

    def install_skill_package(self, zip_path: str, user, source: str = 'user') -> Tuple[bool, str, Optional[Dict]]:
        from Django_xm.apps.tools.models import SkillPackage, ToolCategory

        is_valid, msg, frontmatter = self.validate_skill_package(zip_path)
        if not is_valid:
            return False, msg, None

        name = frontmatter['name']
        description = frontmatter.get('description', '')
        version = str(frontmatter.get('metadata', {}).get('version', '1.0.0')) if isinstance(frontmatter.get('metadata'), dict) else '1.0.0'
        license_str = frontmatter.get('license', '')
        compatibility = frontmatter.get('compatibility', '')
        metadata_json = frontmatter.get('metadata', {})
        allowed_tools = frontmatter.get('allowed-tools', '')

        existing = SkillPackage.objects.filter(user=user, name=name).first()
        if existing:
            self._remove_skill_dir(existing.skill_dir)

        target_dir = os.path.join(self.skills_base_dir, str(user.id), name)
        os.makedirs(target_dir, exist_ok=True)

        try:
            category = ToolCategory.objects.get(code='general')

            with zipfile.ZipFile(zip_path, 'r') as zf:
                skill_md_candidates = [n for n in zf.namelist() if n.endswith('SKILL.md') and '__MACOSX' not in n]
                if skill_md_candidates:
                    skill_md_name = skill_md_candidates[0]
                    parts = skill_md_name.split('/')
                    non_empty = [p for p in parts if p]
                    if len(non_empty) >= 2:
                        prefix = '/'.join(parts[:parts.index(non_empty[0]) + 1]) + '/'
                    else:
                        prefix = ''

                    for member in zf.namelist():
                        if '__MACOSX' in member or member.endswith('/'):
                            continue
                        if prefix and member.startswith(prefix):
                            relative = member[len(prefix):]
                        else:
                            relative = member
                        if not relative:
                            continue

                        target_path = os.path.join(target_dir, relative)
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with open(target_path, 'wb') as f:
                            f.write(zf.read(member))

            # 创建或更新数据库记录
            pkg, created = SkillPackage.objects.update_or_create(
                user=user,
                name=name,
                defaults={
                    'description': description[:500],
                    'version': version[:20],
                    'license': license_str[:200],
                    'compatibility': compatibility[:500],
                    'metadata_json': metadata_json,
                    'allowed_tools': allowed_tools[:1000],
                    'skill_dir': target_dir,
                    'tool_type': 'skill',
                    'category': category,
                    'source': source,
                    'status': 'active',
                }
            )

            action = '安装' if created else '更新'
            logger.info(f"Skill '{name}' {action}成功 (user={user.id}, dir={target_dir})")

            return True, f"Skill '{name}' {action}成功", {
                'name': name,
                'description': description,
                'version': version,
                'source': source,
            }

        except Exception as e:
            # 清理失败的解压目录
            if os.path.isdir(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            logger.error(f"安装 Skill 失败: {e}")
            return False, f"安装失败: {e}", None

    def uninstall_skill_package(self, name: str, user) -> Tuple[bool, str]:
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

        if pkg.source == 'system':
            return False, f"系统级 Skill '{name}' 不可删除"

        # 删除文件目录
        self._remove_skill_dir(pkg.skill_dir)

        # 删除数据库记录
        pkg.delete()

        logger.info(f"Skill '{name}' 已卸载 (user={user.id})")
        return True, f"Skill '{name}' 已卸载"

    # ---- 内部方法 ----

    def _get_skill_dir(self, name: str, user_id: Optional[int] = None) -> Optional[str]:
        """获取 Skill 的文件系统目录路径"""
        from Django_xm.apps.tools.models import SkillPackage
        qs = SkillPackage.objects.filter(name=name, status='active')
        if user_id is not None:
            qs = qs.filter(user_id=user_id) | qs.filter(source='system')
        pkg = qs.first()
        if pkg and pkg.skill_dir and os.path.isdir(pkg.skill_dir):
            return pkg.skill_dir
        return None

    def _read_skill_md(self, path: str) -> str:
        """读取 SKILL.md 并返回 body 部分（指令内容，不含 frontmatter）"""
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        _, body = self._parse_frontmatter(content)
        return body or ''

    @staticmethod
    def _parse_frontmatter(content: str) -> Tuple[Optional[Dict], str]:
        """解析 SKILL.md 的 YAML frontmatter 和 Markdown body

        Returns:
            (frontmatter_dict, body_text)
        """
        # 匹配 --- ... --- 模式
        pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n?(.*)', re.DOTALL)
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
    def _remove_skill_dir(skill_dir: str) -> None:
        """安全删除 Skill 目录"""
        if skill_dir and os.path.isdir(skill_dir):
            try:
                shutil.rmtree(skill_dir)
            except Exception as e:
                logger.warning(f"删除 Skill 目录失败: {skill_dir}, {e}")
