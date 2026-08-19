import re

from rest_framework import serializers

# 资源 name 白名单：ASCII 字母 / 数字 / 中文 / 下划线 / 中划线 / 点 / 空格。
# REST 资源化后 name 作为资源标识符进入 URL path，创建入口必须拒绝
# "/" 与控制字符等路径不安全字符，防止路由失效与路径注入。
RESOURCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff_\-. ]+$")

RESOURCE_NAME_ERROR_MESSAGE = "名称仅允许字母、数字、中文、下划线、中划线、点与空格，不能包含斜杠等路径不安全字符"


def validate_resource_name(value: str) -> None:
    """校验资源 name 为路径安全字符串，不合法时抛出 DRF ValidationError。"""
    if not RESOURCE_NAME_PATTERN.match(value):
        raise serializers.ValidationError(RESOURCE_NAME_ERROR_MESSAGE)


class McpServerAddSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, help_text="MCP Server 名称")
    transport = serializers.ChoiceField(
        choices=["sse", "stdio", "http", "websocket"],
        default="sse",
        help_text="传输协议",
    )
    url = serializers.URLField(required=False, allow_blank=True, help_text="服务器 URL（sse/http/websocket）")
    command = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="可执行命令（stdio）")
    args = serializers.ListField(child=serializers.CharField(), required=False, default=list, help_text="命令参数")
    env = serializers.DictField(required=False, default=dict, help_text="环境变量")
    headers = serializers.DictField(required=False, default=dict, help_text="请求头")
    auth_token = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="认证 Token")
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="", help_text="描述")
    category = serializers.CharField(max_length=50, required=False, default="general", help_text="功能分类编码")

    def validate_name(self, value):
        validate_resource_name(value)
        return value

    def validate(self, data):
        transport = data.get("transport", "sse")
        if transport in ("sse", "http", "websocket") and not data.get("url"):
            raise serializers.ValidationError({"url": f"{transport} 传输协议必须提供 url"})
        if transport == "stdio" and not data.get("command"):
            raise serializers.ValidationError({"command": "stdio 传输协议必须提供 command"})
        return data


class McpToolUploadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, help_text="工具名称")
    code = serializers.CharField(help_text="工具代码")
    description = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default="", help_text="工具描述"
    )
    category = serializers.CharField(max_length=50, required=False, default="general", help_text="功能分类编码")

    def validate_name(self, value):
        validate_resource_name(value)
        return value


class McpServerDiscoverSerializer(serializers.Serializer):
    registry_url = serializers.URLField(help_text="远程注册中心 URL")


class SkillStepSerializer(serializers.Serializer):
    """Skill 步骤序列化器"""

    tool_name = serializers.CharField(max_length=100, help_text="工具名称")
    args_template = serializers.DictField(required=False, default=dict, help_text="参数模板")
    condition = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=None, help_text="执行条件表达式", allow_null=True
    )
    resource_path = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        default=None,
        help_text="Level 3 资源文件路径",
        allow_null=True,
    )


class SkillCreateSerializer(serializers.Serializer):
    """创建自定义 Skill 序列化器"""

    name = serializers.CharField(max_length=100, help_text="Skill 名称")
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="", help_text="描述")
    mode = serializers.ChoiceField(
        choices=["pipeline", "advisor", "hybrid"], required=False, default="pipeline", help_text="执行模式"
    )
    steps = serializers.ListField(child=SkillStepSerializer(), help_text="执行步骤列表")
    version = serializers.CharField(max_length=20, required=False, default="1.0.0", help_text="版本号")
    category = serializers.CharField(max_length=50, required=False, default="general", help_text="功能分类编码")

    def validate_steps(self, value):
        if not value or len(value) == 0:
            raise serializers.ValidationError("至少需要一个执行步骤")
        return value

    def validate_name(self, value):
        if value.startswith("skill_"):
            raise serializers.ValidationError("Skill 名称不能以 skill_ 开头")
        validate_resource_name(value)
        return value


class SkillToggleSerializer(serializers.Serializer):
    """切换 Skill 状态序列化器"""

    name = serializers.CharField(max_length=100, help_text="Skill 名称")
    status = serializers.ChoiceField(choices=["active", "disabled"], required=False, help_text="目标状态")


class SkillPackageUploadSerializer(serializers.Serializer):
    """Skill 包上传序列化器"""

    file = serializers.FileField(help_text="ZIP 格式的 Skill 包")

    def validate_file(self, value):
        if not value.name.endswith(".zip"):
            raise serializers.ValidationError("仅支持 ZIP 格式的 Skill 包")
        return value


class SkillPackageOperationSerializer(serializers.Serializer):
    """Skill 包操作序列化器"""

    name = serializers.CharField(max_length=64, help_text="Skill 名称")
    status = serializers.CharField(max_length=20, required=False, help_text="目标状态")
