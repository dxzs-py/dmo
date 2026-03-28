"""
测试天气功能的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import get_tools_for_request
from langchain_core.messages import HumanMessage, AIMessage


class Command(BaseCommand):
    help = '测试天气功能和上下文记忆'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write('   智能天气查询 + 上下文记忆测试')
        self.stdout.write(self.style.SUCCESS('=' * 70 + '\n'))

        try:
            # 获取工具
            self.stdout.write('正在准备工具...')
            tools = get_tools_for_request(use_tools=True, use_advanced_tools=False)
            
            tool_names = [tool.name for tool in tools]
            if tool_names:
                self.stdout.write(self.style.SUCCESS(f' 已加载工具：{", ".join(tool_names)}\n'))
            else:
                self.stdout.write(self.style.WARNING(' 未加载任何工具\n'))

            # 创建 Agent
            self.stdout.write('正在创建 Agent...\n')
            agent = create_base_agent(tools=tools, prompt_mode='default')

            # 测试 1：上下文记忆
            self.stdout.write(self.style.SUCCESS('测试场景 1：上下文记忆'))
            self.stdout.write(self.style.SUCCESS('-' * 70))

            chat_history = []

            # 第一轮：询问明天深圳的天气
            user_msg_1 = '帮我查询一下明天深圳的天气'
            self.stdout.write(f'\n👤 用户: {user_msg_1}')
            
            response_1 = agent.invoke(
                input_text=user_msg_1,
                chat_history=[],
            )
            
            self.stdout.write(f'🤖 助手: {response_1}\n')

            chat_history.append(HumanMessage(content=user_msg_1))
            chat_history.append(AIMessage(content=response_1))

            # 第二轮：询问后天（应该自动记住深圳）
            user_msg_2 = '后天呢？'
            self.stdout.write(f'👤 用户: {user_msg_2}')
            
            response_2 = agent.invoke(
                input_text=user_msg_2,
                chat_history=chat_history,
            )
            
            self.stdout.write(f'🤖 助手: {response_2}\n')

            chat_history.append(HumanMessage(content=user_msg_2))
            chat_history.append(AIMessage(content=response_2))

            # 第三轮：询问今天（应该继续记住深圳）
            user_msg_3 = '那今天怎么样？'
            self.stdout.write(f'👤 用户: {user_msg_3}')
            
            response_3 = agent.invoke(
                input_text=user_msg_3,
                chat_history=chat_history,
            )
            
            self.stdout.write(f'🤖 助手: {response_3}\n')

            self.stdout.write(self.style.SUCCESS('\n✅ 上下文记忆测试完成！\n'))

            # 测试 2：单日天气查询
            self.stdout.write(self.style.SUCCESS('测试场景 2：单日天气查询'))
            self.stdout.write(self.style.SUCCESS('-' * 70))

            test_queries = [
                '明天北京天气怎么样？',
                '后天上海会下雨吗？',
                '今天广州的温度是多少？',
            ]

            for query in test_queries:
                self.stdout.write(f'\n👤 用户: {query}')
                
                response = agent.invoke(input_text=query, chat_history=[])
                self.stdout.write(f'🤖 助手: {response}\n')

            self.stdout.write(self.style.SUCCESS('\n✅ 单日天气查询测试完成！'))
            self.stdout.write(self.style.SUCCESS('\n🎉 所有测试完成！'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 测试失败：{str(e)}'))
            import traceback
            traceback.print_exc()
            raise CommandError(str(e))
