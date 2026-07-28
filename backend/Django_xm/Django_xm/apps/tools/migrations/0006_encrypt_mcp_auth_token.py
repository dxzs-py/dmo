"""
迁移 0006：McpServerConfig.auth_token 改为加密存储

变更内容：
1. AlterField：将 auth_token 从 CharField(max_length=500) 改为 EncryptedCharField(max_length=500)
   - DB 列类型从 varchar(500) 变为 text（PostgreSQL 自动转换，无数据丢失）
2. RunPython：将现有明文 auth_token 加密为 Fernet token

前置条件：
- 必须设置 FIELD_ENCRYPTION_KEY 环境变量（Fernet 兼容的 base64 编码 32 字节）
  生成命令：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

不可逆：本迁移为安全增强，不支持回滚到明文存储。
"""

from django.db import migrations

import Django_xm.apps.tools.fields


def encrypt_existing_auth_tokens(apps, schema_editor):
    """将现有明文 auth_token 加密为 Fernet token。

    幂等性：
    - 明文 → 加密：正常处理
    - 已加密（当前密钥）→ from_db_value 解密为明文，save() 重新加密（密文不同但语义等价）
    - 已加密（旧密钥）→ from_db_value 解密失败回退原值，save() 视为明文重新加密（双层加密）
      此场景需在密钥轮换流程中单独处理，本迁移不涉及
    """
    # 启动前显式校验密钥，避免静默落库明文
    from Django_xm.apps.tools.fields import _get_fernet
    _get_fernet()

    McpServerConfig = apps.get_model('tools', 'McpServerConfig')
    qs = McpServerConfig.objects.exclude(auth_token='').exclude(auth_token__isnull=True)
    for cfg in qs.iterator():
        # auth_token 已通过 from_db_value 解密为明文（或回退为原值）
        # save() 触发 get_prep_value 加密为 Fernet token
        cfg.save(update_fields=['auth_token'])


class Migration(migrations.Migration):
    """加密 McpServerConfig.auth_token 字段。"""

    dependencies = [
        ('tools', '0005_make_category_non_nullable'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mcpserverconfig',
            name='auth_token',
            field=Django_xm.apps.tools.fields.EncryptedCharField(
                blank=True,
                default='',
                max_length=500,
                verbose_name='认证 Token',
            ),
        ),
        migrations.RunPython(
            encrypt_existing_auth_tokens,
            migrations.RunPython.noop,  # 安全增强迁移不支持回滚到明文
        ),
    ]
