# Generated manually to add missing mode field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0002_add_title_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='mode',
            field=models.CharField(default='basic-agent', max_length=50, verbose_name='对话模式'),
            preserve_default=False,
        ),
    ]
