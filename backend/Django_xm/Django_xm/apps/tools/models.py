from django.conf import settings
from django.db import models

from Django_xm.apps.tools.fields import EncryptedCharField


class ToolCategory(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="分类编码")
    name = models.CharField(max_length=100, verbose_name="分类名称")
    description = models.TextField(blank=True, default="", verbose_name="描述")
    icon = models.CharField(max_length=100, blank=True, default="", verbose_name="图标")
    sort_order = models.IntegerField(default=0, verbose_name="排序")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")

    class Meta:
        db_table = "tools_category"
        ordering = ["sort_order"]
        verbose_name = "功能分类"
        verbose_name_plural = "功能分类"

    def __str__(self):
        return self.name


class UserToolResource(models.Model):
    """用户工具资源抽象基类

    公共字段：user / name / description / tool_type / category / source / status / created_at / updated_at
    """

    class ToolType(models.TextChoices):
        LANGCHAIN = "langchain", "LangChain 工具"
        MCP = "mcp", "MCP 工具"
        SKILL = "skill", "Skill 工具"

    class Source(models.TextChoices):
        SYSTEM = "system", "系统内置"
        USER = "user", "用户上传"
        MARKETPLACE = "marketplace", "市场安装"

    class ToolStatus(models.TextChoices):
        ACTIVE = "active", "已激活"
        DISABLED = "disabled", "已禁用"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="所属用户",
    )
    name = models.CharField(max_length=100, verbose_name="名称")
    description = models.CharField(max_length=500, blank=True, default="", verbose_name="描述")
    tool_type = models.CharField(
        max_length=20,
        choices=ToolType.choices,
        db_index=True,
        default=ToolType.LANGCHAIN,
        verbose_name="工具类型",
    )
    category = models.ForeignKey(
        ToolCategory,
        on_delete=models.PROTECT,
        related_name="%(class)s_set",
        verbose_name="功能分类",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.USER,
        db_index=True,
        verbose_name="来源",
    )
    status = models.CharField(
        max_length=20,
        choices=ToolStatus.choices,
        default=ToolStatus.ACTIVE,
        verbose_name="状态",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class CustomTool(UserToolResource):
    """用户自定义工具模型

    用户上传的代码需经管理员审核（approval_status）通过后才生效：
        - pending: 新上传，等待审核（不会加载为可执行工具）
        - approved: 管理员通过，可加载为 BaseTool
        - rejected: 管理员拒绝，不会加载

    审核状态与 status 字段独立：
        - status: 运行时启用/禁用（用户自行切换）
        - approval_status: 管理员审核结果（决定是否加载）
    """

    class ApprovalStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已拒绝"

    code = models.TextField(verbose_name="工具代码")
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
        verbose_name="审核状态",
    )

    class Meta(UserToolResource.Meta):
        db_table = "tools_custom_tool"
        verbose_name = "自定义工具"
        verbose_name_plural = "自定义工具"
        unique_together = [("user", "name")]

    @property
    def is_effective(self) -> bool:
        """工具是否实际生效（已审核通过且处于 active 状态）"""
        return self.approval_status == self.ApprovalStatus.APPROVED and self.status == "active"


class McpServerConfig(UserToolResource):
    """用户自定义 MCP Server 配置模型"""

    class Transport(models.TextChoices):
        SSE = "sse", "SSE"
        STDIO = "stdio", "STDIO"
        HTTP = "http", "HTTP"
        WEBSOCKET = "websocket", "WebSocket"

    transport = models.CharField(
        max_length=20,
        choices=Transport.choices,
        default=Transport.SSE,
        verbose_name="传输协议",
    )
    url = models.URLField(blank=True, default="", verbose_name="服务器 URL")
    command = models.CharField(max_length=500, blank=True, default="", verbose_name="可执行命令")
    args = models.JSONField(default=list, blank=True, verbose_name="命令参数")
    env = models.JSONField(default=dict, blank=True, verbose_name="环境变量")
    headers = models.JSONField(default=dict, blank=True, verbose_name="请求头")
    auth_token = EncryptedCharField(max_length=500, blank=True, default="", verbose_name="认证 Token")

    class Meta(UserToolResource.Meta):
        db_table = "tools_mcp_server_config"
        verbose_name = "MCP Server 配置"
        verbose_name_plural = "MCP Server 配置"
        unique_together = [("user", "name")]

    def __str__(self):
        return f"{self.name} ({self.user.username}, {self.transport})"

    def to_config_dict(self):
        """转换为 MCP 连接配置字典"""
        config = {
            "name": self.name,
            "transport": self.transport,
            "description": self.description,
            "enabled": self.status == "active",
        }
        if self.transport == "stdio":
            config["command"] = self.command
            config["args"] = self.args or []
            if self.env:
                config["env"] = self.env
        else:
            if self.url:
                config["url"] = self.url
            if self.headers:
                config["headers"] = self.headers
            if self.auth_token:
                config["auth_token"] = self.auth_token
        return config


class SkillConfig(UserToolResource):
    """用户自定义 Skill 配置模型"""

    class Mode(models.TextChoices):
        PIPELINE = "pipeline", "管线模式"
        ADVISOR = "advisor", "顾问模式"
        HYBRID = "hybrid", "混合模式"

    mode = models.CharField(
        max_length=20,
        choices=Mode.choices,
        default=Mode.PIPELINE,
        verbose_name="执行模式",
    )
    type = models.CharField(max_length=20, default="pipeline", verbose_name="类型")
    steps = models.JSONField(
        verbose_name="执行步骤",
        help_text="SkillStep 列表，每项包含 tool_name, args_template, condition",
        default=list,
    )
    version = models.CharField(
        max_length=20,
        default="1.0.0",
        verbose_name="版本号",
    )

    class Meta(UserToolResource.Meta):
        db_table = "tools_skill_config"
        verbose_name = "工具管道"
        verbose_name_plural = "工具管道"
        unique_together = [("user", "name")]

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    def to_skill_definition(self):
        """转换为 SkillSpec 对象"""
        from Django_xm.apps.tools.skills.registry import SkillSpec, SkillStep

        skill_steps = []
        for step_data in self.steps:
            if isinstance(step_data, dict):
                skill_steps.append(
                    SkillStep(
                        tool_name=step_data.get("tool_name", ""),
                        args_template=step_data.get("args_template", {}),
                        condition=step_data.get("condition"),
                        resource_path=step_data.get("resource_path"),
                    )
                )
        return SkillSpec(
            name=self.name,
            description=self.description or "",
            mode=self.mode,
            steps=skill_steps,
            version=self.version,
            source=self.source,
        )


class SkillPackage(UserToolResource):
    """Agent Skills 规范的 Skill 包模型"""

    version = models.CharField(max_length=20, default="1.0.0", verbose_name="版本号")
    license = models.CharField(max_length=200, blank=True, default="", verbose_name="许可证")
    compatibility = models.CharField(max_length=500, blank=True, default="", verbose_name="兼容性要求")
    metadata_json = models.JSONField(default=dict, blank=True, verbose_name="元数据")
    allowed_tools = models.CharField(max_length=1000, blank=True, default="", verbose_name="预批准工具列表")
    skill_dir = models.CharField(max_length=500, verbose_name="Skill 包文件系统路径")

    class Meta(UserToolResource.Meta):
        db_table = "tools_skill_package"
        verbose_name = "Skill 技能包"
        verbose_name_plural = "Skill 技能包"
        unique_together = [("user", "name")]

    def __str__(self):
        return f"{self.name} ({self.user.username}, {self.source})"
