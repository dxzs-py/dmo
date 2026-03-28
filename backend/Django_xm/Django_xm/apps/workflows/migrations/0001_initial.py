# Workflows 数据库迁移脚本

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='WorkflowExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('thread_id', models.CharField(max_length=100, unique=True, verbose_name='线程 ID')),
                ('workflow_type', models.CharField(max_length=50, verbose_name='工作流类型')),
                ('query', models.TextField(verbose_name='查询内容')),
                ('status', models.CharField(choices=[('pending', '待执行'), ('running', '执行中'), ('completed', '已完成'), ('failed', '失败')], default='pending', max_length=20, verbose_name='状态')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('result', models.JSONField(blank=True, null=True, verbose_name='执行结果')),
            ],
            options={
                'db_table': 'workflow_execution',
                'verbose_name': '工作流执行',
                'verbose_name_plural': '工作流执行',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WorkflowSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('thread_id', models.CharField(max_length=100, unique=True, verbose_name='线程 ID')),
                ('user_question', models.TextField(verbose_name='用户问题')),
                ('status', models.CharField(choices=[('running', '执行中'), ('waiting_for_answers', '等待答案'), ('retry', '重试'), ('completed', '已完成'), ('failed', '失败')], default='running', max_length=20, verbose_name='状态')),
                ('current_step', models.CharField(blank=True, max_length=50, verbose_name='当前步骤')),
                ('learning_plan', models.JSONField(blank=True, null=True, verbose_name='学习计划')),
                ('quiz', models.JSONField(blank=True, null=True, verbose_name='练习题')),
                ('user_answers', models.JSONField(blank=True, null=True, verbose_name='用户答案')),
                ('score', models.IntegerField(blank=True, null=True, verbose_name='得分')),
                ('score_details', models.JSONField(blank=True, null=True, verbose_name='评分详情')),
                ('feedback', models.TextField(blank=True, verbose_name='反馈信息')),
                ('should_retry', models.BooleanField(default=False, verbose_name='是否重试')),
                ('error_message', models.TextField(blank=True, verbose_name='错误信息')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'db_table': 'workflow_session',
                'verbose_name': '工作流会话',
                'verbose_name_plural': '工作流会话',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='workflowexecution',
            index=models.Index(fields=['thread_id'], name='workflow_exec_thread__b63f37_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowexecution',
            index=models.Index(fields=['workflow_type'], name='workflow_exec_workflo_7e5d77_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowexecution',
            index=models.Index(fields=['status'], name='workflow_exec_status_4e0b8a_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowexecution',
            index=models.Index(fields=['created_at'], name='workflow_exec_created_7c6e14_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowexecution',
            index=models.Index(fields=['workflow_type', 'status', '-created_at'], name='workflow_exec_type_st_5c2a1f_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowsession',
            index=models.Index(fields=['thread_id'], name='workflow_sess_thread__0c9a2b_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowsession',
            index=models.Index(fields=['status'], name='workflow_sess_status_1d8b3c_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowsession',
            index=models.Index(fields=['created_at'], name='workflow_sess_created_2e7c4d_idx'),
        ),
        migrations.AddIndex(
            model_name='workflowsession',
            index=models.Index(fields=['status', '-created_at'], name='workflow_sess_status_3f6d5e_idx'),
        ),
    ]

