from django.db import migrations


def populate_classification_data(apps, schema_editor):
    ToolCategory = apps.get_model('tools', 'ToolCategory')
    CustomTool = apps.get_model('tools', 'CustomTool')
    McpServerConfig = apps.get_model('tools', 'McpServerConfig')
    SkillConfig = apps.get_model('tools', 'SkillConfig')
    SkillPackage = apps.get_model('tools', 'SkillPackage')

    categories_data = [
        {'code': 'basic', 'name': '基础工具', 'sort_order': 1},
        {'code': 'web_search', 'name': '网络搜索', 'sort_order': 2},
        {'code': 'file', 'name': '文件操作', 'sort_order': 3},
        {'code': 'weather', 'name': '天气查询', 'sort_order': 4},
        {'code': 'translation', 'name': '翻译', 'sort_order': 5},
        {'code': 'agent', 'name': '代理助手', 'sort_order': 6},
        {'code': 'todo', 'name': '待办管理', 'sort_order': 7},
        {'code': 'web_fetch', 'name': '网页抓取', 'sort_order': 8},
        {'code': 'general', 'name': '通用', 'sort_order': 9},
    ]
    for cat_data in categories_data:
        ToolCategory.objects.update_or_create(
            code=cat_data['code'],
            defaults=cat_data,
        )

    general_cat = ToolCategory.objects.get(code='general')

    CustomTool.objects.filter(category__isnull=True).update(
        tool_type='langchain',
        category=general_cat.id,
        source='user',
    )

    McpServerConfig.objects.filter(category__isnull=True).update(
        tool_type='mcp',
        category=general_cat.id,
        source='user',
    )

    SkillConfig.objects.filter(category__isnull=True).update(
        tool_type='skill',
        category=general_cat.id,
        source='user',
    )

    for pkg in SkillPackage.objects.filter(category__isnull=True):
        old_source = getattr(pkg, 'source', 'user')
        pkg.tool_type = 'skill'
        pkg.category = general_cat
        pkg.source = old_source
        pkg.save(update_fields=['tool_type', 'category', 'source'])


def reverse_classification_data(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tools", "0003_add_tool_category_and_classification_fields"),
    ]

    operations = [
        migrations.RunPython(populate_classification_data, reverse_classification_data),
    ]
