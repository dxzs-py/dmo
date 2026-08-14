"""审批序列化器。

按读/写职责拆分为两个序列化器：
- ``ApprovalReadSerializer``：GET /approvals/、/approvals/{id}/、/approvals/{id}/state/，
  暴露全部展示字段，全部 read_only。
- ``ApprovalWriteSerializer``：POST /approvals/{id}/resume/、/approvals/{id}/reject/，
  白名单设计：客户端仅可提交 ``approved`` 与 ``user_input`` 两个字段，
  其他审批字段（state、parameters、approved_by、source 等）由服务端控制，
  客户端尝试提交 protected 字段会触发 ``to_internal_value`` 显式拒绝（返回 400），
  而非静默忽略——便于日志审计与暴露潜在恶意行为。
"""

from rest_framework import serializers

from Django_xm.apps.approvals.models import Approval


class ApprovedBySerializer(serializers.Serializer):
    """审批人简要信息。"""

    id = serializers.IntegerField()
    username = serializers.CharField()


class ApprovalReadSerializer(serializers.ModelSerializer):
    """审批读序列化器。

    用于所有 GET 端点（list/detail/state），暴露全部展示字段。
    全部字段标记 read_only，客户端无法通过任何端点修改审批字段。
    """

    approved_by = ApprovedBySerializer(read_only=True)

    class Meta:
        model = Approval
        fields = [
            "interrupt_id",
            "source",
            "source_id",
            "chat_session_id",
            "tool_name",
            "title",
            "description",
            "action",
            "operation",
            "danger_level",
            "parameters",
            "state",
            "user_input",
            "approved_by",
            "extra",
            "created_at",
            "resolved_at",
        ]
        # 全部 read_only：list/detail 视图仅用于读取，禁止任何写入
        read_only_fields = fields


# 客户端不允许通过 API 设置的审批字段（白名单之外的 protected 字段）
# 任一字段被客户端提交时，ApprovalWriteSerializer.to_internal_value 显式拒绝
# 注意：approved 与 user_input 是客户端可提交字段，故不在本集合中
_PROTECTED_APPROVAL_FIELDS = frozenset(
    {
        "interrupt_id",
        "source",
        "source_id",
        "chat_session_id",
        "tool_name",
        "title",
        "description",
        "action",
        "operation",
        "danger_level",
        "parameters",
        "state",
        "approved_by",
        "approved_by_id",
        "user",
        "user_id",
        "extra",
        "created_at",
        "resolved_at",
        "expires_at",
    }
)


class ApprovalWriteSerializer(serializers.Serializer):
    """审批写序列化器。

    白名单设计：客户端仅可提交 ``approved`` 与 ``user_input``。

    - ``approved``：是否批准（仅 resume 接口读取；reject 接口忽略并强制 False）
    - ``user_input``：用户输入文本（仅当 approval.action == confirm_with_input 时使用）

    其他审批字段（state、parameters、approved_by、source 等）由服务端控制，
    客户端尝试提交 protected 字段会触发 ``to_internal_value`` 显式拒绝（返回 400），
    而非静默忽略——便于日志审计与暴露潜在恶意行为。
    """

    approved = serializers.BooleanField(default=True, required=False)
    user_input = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=10000,
    )

    def to_internal_value(self, data):
        """防御性深度：客户端尝试设置 protected 字段时显式拒绝。

        DRF 默认会忽略未声明的字段，但攻击者通过提交 protected 字段
        仍可能在日志中造成混淆或绕过未来可能引入的逻辑。本方法显式
        检测 protected 字段并返回 400，暴露潜在恶意行为。
        """
        if isinstance(data, dict):
            attempted = _PROTECTED_APPROVAL_FIELDS & set(data.keys())
            if attempted:
                raise serializers.ValidationError(
                    {field: f"{field} 字段不允许客户端设置" for field in sorted(attempted)}
                )
        return super().to_internal_value(data)

    def validate_user_input(self, value):
        """user_input 类型校验（allow_null=True 允许 None）。"""
        if value is not None and not isinstance(value, str):
            raise serializers.ValidationError("user_input 必须为字符串")
        return value

    def validate(self, attrs):
        """整体校验：防御性深度。

        正常情况下 source/action 不会出现在 attrs 中（被 to_internal_value 拒绝），
        但仍校验 attrs 中所有受控字段（防御性深度，防止未来扩展引入漏洞）。
        """
        valid_sources = {
            Approval.SOURCE_CHAT,
            Approval.SOURCE_DEEP_RESEARCH,
        }
        valid_actions = {Approval.ACTION_CONFIRM, Approval.ACTION_CONFIRM_WITH_INPUT}

        if "source" in attrs and attrs["source"] not in valid_sources:
            raise serializers.ValidationError({"source": "非法的审批来源"})
        if "action" in attrs and attrs["action"] not in valid_actions:
            raise serializers.ValidationError({"action": "非法的审批动作"})
        return attrs
