
"""
测试天气功能和上下文记忆的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import get_tools_for_request
from langchain_core.messages import HumanMessage, AIMessage


class Command(BaseCommand):
    help = '测试天气功能和上下文记忆'

    def add_arguments(self, parser):
        parser.add_argument(
            '--city',
            type=str,
            default='北京',
            help='城市名称'
        )
        parser.add_argument(
            '--mode',
            type=str,
            default='default',
            help='Agent 模式：default, coding, research, concise, detailed'
        )

    def handle(self, *args, **options):
        city = options['city']
        mode = options.get('mode', 'default')

        self.stdout.write(self.style.SUCCESS('=== 天气功能和上下文记忆测试 ==='))
        self.stdout.write(f'测试城市：{city}')
        self.stdout.write(f'模式：{mode}')
        self.stdout.write('\n')

        try:
            # 获取工具列表
            self.stdout.write('正在准备工具...')
            tools = get_tools_for_request(use_tools=True, use_advanced_tools=False)
            tool_names = [tool.name for tool in tools]
            if tool_names:
                self.stdout.write(self.style.SUCCESS(f' 已加载工具：{", ".join(tool_names)}'))
            else:
                self.stdout.write(self.style.WARNING(' 未加载任何工具'))
            
            # 创建 Agent
            self.stdout.write('正在创建 Agent...')
            agent = create_base_agent(tools=tools, prompt_mode=mode)
            
            # 测试1: 询问今天的天气
            self.stdout.write(self.style.SUCCESS('\n=== 测试1: 询问天气 ==='))
            response1 = agent.invoke(
                input_text=f"{city}今天天气怎么样？",
                chat_history=[],
            )
            self.stdout.write(f'用户: {city}今天天气怎么样？')
            self.stdout.write(f'助手: {response1}')
            self.stdout.write('\n')
            
            # 测试2: 询问明天的天气（验证上下文记忆 - 记住城市）
            self.stdout.write(self.style.SUCCESS('=== 测试2: 继续对话（验证上下文记忆） ==='))
            chat_history = [
                HumanMessage(content=f"{city}今天天气怎么样？"),
                AIMessage(content=response1),
            ]
            response2 = agent.invoke(
                input_text="那明天呢？",
                chat_history=chat_history,
            )
            self.stdout.write('用户: 那明天呢？')
            self.stdout.write(f'助手: {response2}')
            self.stdout.write('\n')
            
            # 测试3: 询问后天的天气
            self.stdout.write(self.style.SUCCESS('=== 测试3: 再继续对话 ==='))
            chat_history.extend([
                HumanMessage(content="那明天呢？"),
                AIMessage(content=response2),
            ])
            response3 = agent.invoke(
                input_text="后天呢？",
                chat_history=chat_history,
            )
            self.stdout.write('用户: 后天呢？')
            self.stdout.write(f'助手: {response3}')
            
            self.stdout.write(self.style.SUCCESS('\n\n✅ 天气功能和上下文记忆测试成功完成'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 测试失败：{str(e)}'))
            import traceback
            traceback.print_exc()
            raise CommandError(str(e))

