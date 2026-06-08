from django.contrib import admin
from .models import SystemConfig, AIProvider, AIModel, EmbeddingProviderConfig


@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'updated_at')
    search_fields = ('key',)
    readonly_fields = ('updated_at',)


class AIModelInline(admin.TabularInline):
    model = AIModel
    extra = 1
    fields = ('name', 'capabilities', 'is_enabled', 'sort_order')
    ordering = ('sort_order', 'name')
    verbose_name = 'LLM 模型'
    verbose_name_plural = 'LLM 模型列表'


class EmbeddingConfigInline(admin.TabularInline):
    model = EmbeddingProviderConfig
    extra = 0
    fields = (
        'name',
        'default_model',
        'dimension',
        'native_max_dimension',
        'min_dimension',
        'is_enabled',
        'sort_order',
    )
    verbose_name = 'Embedding 配置'
    verbose_name_plural = 'Embedding 配置列表'


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ('provider_id', 'label', 'provider', 'default_model', 'has_embedding', 'is_enabled', 'sort_order')
    list_filter = ('is_enabled', 'provider')
    search_fields = ('provider_id', 'label')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [AIModelInline, EmbeddingConfigInline]
    fieldsets = (
        ('基本信息', {
            'fields': ('provider_id', 'provider', 'label', 'icon', 'default_model', 'is_enabled', 'sort_order'),
            'description': (
                '<b style="color:#c00">provider_id 和 provider 创建后不可修改</b>，'
                '它们是系统内部标识，修改会导致模型创建失败。如需更换请删除重建。'
            ),
        }),
        ('API 配置', {
            'fields': ('api_key_attr', 'base_url_attr'),
            'description': (
                'API Key 与 Base URL 通过 Pydantic Settings 读取（.env / 环境变量），'
                '请确保 settings 中存在对应属性。'
                '<br><b>已废弃字段</b>：<code>api_key_env</code> / <code>model_attr</code> '
                '保留在模型层以兼容历史数据，业务代码不再消费，请忽略。'
            ),
        }),
        ('高级配置', {
            'fields': ('special_params', 'presets'),
            'classes': ('collapse',),
            'description': 'special_params 控制模型特殊参数（如 DeepSeek 思考模式），presets 定义预设配置。修改时请参考已有格式。',
        }),
        ('时间', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    actions = ['invalidate_registry_cache']

    @admin.display(boolean=True, description='Embedding')
    def has_embedding(self, obj):
        return obj.embedding_configs.exists()

    @admin.action(description='清除注册表缓存')
    def invalidate_registry_cache(self, request, queryset):
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()
        self.message_user(request, '注册表缓存已清除')

    def get_readonly_fields(self, request, obj=None):
        """创建后 provider_id 和 provider 不可修改"""
        base = list(super().get_readonly_fields(request, obj))
        if obj:  # 编辑模式
            base.extend(['provider_id', 'provider'])
        return base

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_queryset(self, request, queryset):
        # 批量删除（actions → "删除选中项"）走 delete_queryset 而非 delete_model，
        # 必须显式清除注册表缓存，否则前端下拉框会残留已删除项。
        super().delete_queryset(request, queryset)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'name', 'provider', 'is_enabled', 'sort_order')
    list_filter = ('provider', 'is_enabled')
    search_fields = ('name',)
    readonly_fields = ('created_at',)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_queryset(self, request, queryset):
        # 批量删除必须显式清缓存，否则 LLM 列表与下拉框会残留已删除项
        super().delete_queryset(request, queryset)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()


@admin.register(EmbeddingProviderConfig)
class EmbeddingProviderConfigAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'default_model', 'dimension', 'native_max_dimension', 'is_enabled', 'sort_order')
    list_filter = ('is_enabled', 'provider')
    search_fields = ('provider__provider_id', 'provider__label', 'name', 'default_model')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('所属 Provider', {
            'fields': ('provider', 'name', 'default_model'),
            'description': 'Embedding 与 LLM 属于同一个 Provider，需先创建 LLM Provider。同一 Provider 可挂多个 Embedding 配置。',
        }),
        ('维度能力', {
            'fields': ('native_max_dimension', 'min_dimension'),
            'description': (
                '• <b>native_max_dimension</b>：模型不传 dimensions 时的输出维度（如 nomic-embed-text 768、bge-m3 1024、Qwen3-Embedding-4B 2560）<br>'
                '• <b>min_dimension</b>：MRL 截断下限，0 表示不支持截断（固定维度模型填 0）<br>'
                '• <b>判断 MRL</b>：min_dimension > 0 且 native_max_dimension > 0 时，前端可调整输出维度（范围 [min, max]）<br>'
                '• <b>支持 MRL 的模型</b>：qwen3-embedding / nomic-embed-text / embeddinggemma / OpenAI text-embedding-3-* 等'
            ),
        }),
        ('Embedding 配置', {
            'fields': ('factory_path', 'dimension', 'supported_params', 'is_enabled', 'sort_order'),
            'description': (
                '• <b>工厂函数路径</b>：创建 Embedding 实例的 Python 函数路径，系统通过 importlib 动态调用。更换 Embedding 实现时需修改。<br>'
                '• <b>输出维度</b>：模型默认输出维度（<b>非 MRL 截断维度</b>）。MRL 截断由用户在 <b>前端设置</b> 调整，'
                '此字段主要作为默认 fallback 与历史索引重建时的兜底值。如需修改默认输出维度（如切换到同系列的另一个 embedding 模型）'
                '才需要调整这里。<br>'
                '• <b>MRL 维度调整</b>：<code>min_dimension</code> / <code>native_max_dimension</code> 定义模型能力范围；'
                '用户在 <b>前端设置 → Embedding 配置 → 输出维度</b> 输入框中调整具体数值即可，系统会自动校验范围。'
            ),
        }),
        ('时间', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        """维度能力规则：
        - 新增模式（obj is None）：dimension 保持可编辑
        - 编辑模式：dimension 始终可编辑（管理员切换 embedding 模型时需要修改）；
          用户的 MRL 截断维度由前端调整，不在 admin 范围内
        """
        base = list(super().get_readonly_fields(request, obj))
        return base

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()

    def delete_queryset(self, request, queryset):
        # 批量删除必须显式清缓存，否则 Embedding 下拉框会残留已删除项
        super().delete_queryset(request, queryset)
        from Django_xm.apps.ai_engine.services.registry_service import invalidate_cache
        invalidate_cache()
