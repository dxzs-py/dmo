
"""
测试工作流功能的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.workflows.study_flow import create_study_flow


class Command(BaseCommand):
    help = '测试学习工作流功能'

    def add_arguments(self, parser):
        parser.add_argument(
            '--question',
            type=str,
            required=True,
            help='用户学习问题'
        )
        parser.add_argument(
            '--thread-id',
            type=str,
            help='线程 ID（可选，不提供则自动生成）'
        )

    def handle(self, *args, **options):
        question = options['question']
        thread_id = options.get('thread_id')

        self.stdout.write(self.style.SUCCESS('=== 学习工作流测试 ==='))
        self.stdout.write(f'问题：{question}')
        if thread_id:
            self.stdout.write(f'线程 ID: {thread_id}')
        self.stdout.write('\n')

        try:
            # 创建工作流
            self.stdout.write('正在创建工作流...')
            study_flow = create_study_flow(thread_id=thread_id)
            
            # 准备输入
            inputs = {
                "user_question": question,
                "current_step": "initializing",
                "retry_count": 0
            }
            
            config = {
                "configurable": {"thread_id": thread_id} if thread_id else {}
            }
            
            # 执行工作流
            self.stdout.write('正在执行工作流...')
            result = study_flow.invoke(inputs, config=config)
            
            # 显示结果
            self.stdout.write(self.style.SUCCESS('\n=== 工作流执行完成 ===\n'))
            
            if result.get("error"):
                self.stdout.write(self.style.ERROR(f'错误：{result["error"]}'))
                return
            
            # 显示学习计划
            learning_plan = result.get('learning_plan')
            if learning_plan:
                self.stdout.write(self.style.SUCCESS('=== 学习计划 ==='))
                if isinstance(learning_plan, dict):
                    self.stdout.write(f"简介：{learning_plan.get('introduction', 'N/A')}")
                    self.stdout.write(f"\n步骤：")
                    for i, step in enumerate(learning_plan.get('steps', []), 1):
                        self.stdout.write(f"  {i}. {step}")
                else:
                    self.stdout.write(str(learning_plan))
            
            # 显示练习题
            quiz = result.get('quiz')
            if quiz:
                self.stdout.write(self.style.SUCCESS('\n=== 练习题 ==='))
                if isinstance(quiz, dict):
                    for i, q in enumerate(quiz.get('questions', []), 1):
                        self.stdout.write(f"\n问题 {i}: {q.get('question', 'N/A')}")
                        for j, option in enumerate(q.get('options', []), ord('A')):
                            self.stdout.write(f"  {chr(j)}. {option}")
                else:
                    self.stdout.write(str(quiz))
            
            self.stdout.write(self.style.SUCCESS('\n\n✅ 工作流测试成功完成'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 工作流测试失败：{str(e)}'))
            raise CommandError(str(e))
