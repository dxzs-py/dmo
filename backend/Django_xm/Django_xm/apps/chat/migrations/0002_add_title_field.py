# Generated manually to add missing title field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='title',
            field=models.CharField(default='新对话', max_length=200, verbose_name='会话标题'),
            preserve_default=False,
        ),
    ]
