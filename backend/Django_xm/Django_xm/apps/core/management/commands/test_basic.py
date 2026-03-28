"""
基础功能测试脚本
验证核心功能是否正常工作
"""

from django.core.management.base import BaseCommand
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import BASIC_TOOLS, get_current_time, calculator
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.config import settings


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


class Command(BaseCommand):
    help = '基础功能测试 - 验证配置、模型、工具和Agent'

    def add_arguments(self, parser):
        parser.add_argument(
            '--test',
            type=str,
            choices=['config', 'model', 'tools', 'agent', 'all'],
            default='all',
            help='选择要运行的测试'
        )

    def handle(self, *args, **options):
        test_type = options['test']

        if test_type in ['config', 'all']:
            self.test_config()
        if test_type in ['model', 'all']:
            self.test_model()
        if test_type in ['tools', 'all']:
            self.test_tools()
        if test_type in ['agent', 'all']:
            self.test_agent()

        self.stdout.write(self.style.SUCCESS('\n✅ 所有测试完成！'))

    def test_config(self):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}测试 1: 配置加载{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')

        try:
            self.stdout.write(f'✅ 应用名称: {getattr(settings, "app_name", "N/A")}')
            self.stdout.write(f'✅ 版本: {getattr(settings, "app_version", "N/A")}')
            self.stdout.write(f'✅ 模型: {getattr(settings, "openai_model", "N/A")}')
            self.stdout.write(f'✅ API Base: {getattr(settings, "openai_api_base", "N/A")}')
            self.stdout.write(self.style.SUCCESS('✅ 配置测试通过'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 配置测试失败: {e}'))

    def test_model(self):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}测试 2: 模型创建{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')

        try:
            model = get_chat_model()
            self.stdout.write(f'✅ 模型创建成功: {model.__class__.__name__}')
            self.stdout.write(f'✅ 模型: {getattr(settings, "openai_model", "N/A")}')
            self.stdout.write(self.style.SUCCESS('✅ 模型测试通过'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 模型创建失败: {e}'))

    def test_tools(self):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}测试 3: 工具调用{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')

        try:
            time_result = get_current_time.invoke({})
            self.stdout.write(f'✅ 时间工具: {time_result}')

            calc_result = calculator.invoke({"expression": "2 + 2"})
            self.stdout.write(f'✅ 计算器工具: {calc_result}')

            self.stdout.write(f'✅ 基础工具数量: {len(BASIC_TOOLS)}')
            self.stdout.write(self.style.SUCCESS('✅ 工具测试通过'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 工具测试失败: {e}'))

    def test_agent(self):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}测试 4: Agent 基本功能{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')

        try:
            self.stdout.write('创建 Agent...')
            agent = create_base_agent(
                tools=BASIC_TOOLS,
                prompt_mode='default'
            )
            self.stdout.write('✅ Agent 创建成功')

            self.stdout.write('测试简单对话...')
            response = agent.invoke("你好，简单介绍一下自己")
            self.stdout.write(f'✅ 对话响应长度: {len(response)} 字符')
            self.stdout.write(self.style.SUCCESS('✅ Agent 测试通过'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Agent 测试失败: {e}'))