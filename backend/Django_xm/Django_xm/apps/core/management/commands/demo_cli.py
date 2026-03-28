
"""
CLI 演示工具的 Django management command
用于在命令行中测试和演示 Agent 功能
"""

from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.core.tools import get_tools_for_request
from Django_xm.apps.core.config import settings
from langchain_core.messages import HumanMessage, AIMessage


class Colors:
    """终端颜色代码"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class Command(BaseCommand):
    help = 'CLI 演示工具 - 测试 Agent 功能'

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode',
            type=str,
            default='default',
            help='Agent 模式: default, coding, research, concise, detailed'
        )
        parser.add_argument(
            '--stream',
            action='store_true',
            help='启用流式输出'
        )
        parser.add_argument(
            '--no-tools',
            action='store_true',
            help='禁用工具'
        )

    def handle(self, *args, **options):
        self.print_banner()
        
        mode = options.get('mode', 'default')
        streaming = options.get('stream', False)
        use_tools = not options.get('no_tools', False)
        
        try:
            settings.validate_required_keys()
        except ValueError as e:
            self.stdout.write(self.style.ERROR(f'❌ 配置错误: {e}'))
            self.stdout.write(self.style.WARNING('请在 .env 文件中设置 OPENAI_API_KEY'))
            return
        
        if not settings.tavily_api_key:
            self.stdout.write(self.style.WARNING('⚠️  未配置 Tavily API Key，网络搜索功能将不可用'))
        
        self.print_help()
        
        session = ChatSession(
            self,
            mode=mode,
            streaming=streaming,
            use_tools=use_tools,
            use_advanced_tools=bool(settings.tavily_api_key),
        )
        
        self.main_loop(session)
    
    def print_banner(self):
        banner = f"""{Colors.CYAN}{'=' * 70}{Colors.ENDC}
{Colors.BOLD}  🎓 LC-StudyLab 智能学习助手 - CLI 演示工具{Colors.ENDC}
{Colors.CYAN}  版本: {settings.app_version}
  模型: {settings.openai_model}
{'=' * 70}{Colors.ENDC}
"""
        self.stdout.write(banner)
    
    def print_help(self):
        help_text = f"""{Colors.YELLOW}可用命令:{Colors.ENDC}
  {Colors.GREEN}/help{Colors.ENDC}      - 显示此帮助信息
  {Colors.GREEN}/mode{Colors.ENDC}      - 切换 Agent 模式
  {Colors.GREEN}/stream{Colors.ENDC}    - 切换流式/非流式输出
  {Colors.GREEN}/tools{Colors.ENDC}     - 切换工具启用/禁用
  {Colors.GREEN}/clear{Colors.ENDC}     - 清空对话历史
  {Colors.GREEN}/info{Colors.ENDC}      - 显示当前配置
  {Colors.GREEN}/quit{Colors.ENDC}      - 退出程序

{Colors.YELLOW}快捷测试:{Colors.ENDC}
  {Colors.CYAN}现在几点？{Colors.ENDC}              - 测试时间工具
  {Colors.CYAN}计算 123 + 456{Colors.ENDC}         - 测试计算器工具
  {Colors.CYAN}搜索 LangChain 1.0.3{Colors.ENDC}   - 测试网络搜索

直接输入消息开始对话！
"""
        self.stdout.write(help_text)
    
    def main_loop(self, session):
        while True:
            try:
                self.stdout.write(f'\n{Colors.GREEN}👤 你: {Colors.ENDC}', ending='')
                user_input = input().strip()
                
                if not user_input:
                    continue
                
                if user_input.startswith('/'):
                    command = user_input.lower()
                    
                    if command in ['/quit', '/exit', '/q']:
                        self.stdout.write(f'\n{Colors.CYAN}👋 再见！{Colors.ENDC}')
                        break
                    
                    elif command in ['/help', '/h']:
                        self.print_help()
                    
                    elif command.startswith('/mode'):
                        parts = command.split()
                        if len(parts) > 1:
                            session.set_mode(parts[1])
                        else:
                            self.stdout.write(f'{Colors.YELLOW}用法: /mode &lt;模式名&gt;{Colors.ENDC}')
                            self.stdout.write(f'{Colors.YELLOW}可用模式: default, coding, research, concise, detailed{Colors.ENDC}')
                    
                    elif command == '/stream':
                        session.toggle_streaming()
                    
                    elif command == '/tools':
                        session.toggle_tools()
                    
                    elif command == '/clear':
                        session.clear_history()
                    
                    elif command == '/info':
                        session.show_info()
                    
                    else:
                        self.stdout.write(f'{Colors.RED}❌ 未知命令: {command}{Colors.ENDC}')
                        self.stdout.write(f'{Colors.YELLOW}输入 /help 查看可用命令{Colors.ENDC}')
                    
                    continue
                
                session.chat(user_input)
                
            except KeyboardInterrupt:
                self.stdout.write(f'\n\n{Colors.CYAN}👋 检测到 Ctrl+C，正在退出...{Colors.ENDC}')
                break
            
            except Exception as e:
                self.stdout.write(f'\n{Colors.RED}❌ 错误: {e}{Colors.ENDC}')
                import traceback
                traceback.print_exc()


class ChatSession:
    """聊天会话管理"""
    
    def __init__(self, command, mode: str, streaming: bool, use_tools: bool, use_advanced_tools: bool):
        self.command = command
        self.mode = mode
        self.streaming = streaming
        self.use_tools = use_tools
        self.use_advanced_tools = use_advanced_tools
        self.chat_history = []
        self.agent = None
        
        self._create_agent()
    
    def _create_agent(self):
        """创建或重新创建 Agent"""
        tools = get_tools_for_request(self.use_tools, self.use_advanced_tools)
        
        self.agent = create_base_agent(
            tools=tools,
            prompt_mode=self.mode,
        )
        
        self.command.stdout.write(f'{Colors.GREEN}✅ Agent 已创建: mode={self.mode}, tools={len(tools)}{Colors.ENDC}')
    
    def set_mode(self, mode: str):
        """切换模式"""
        self.mode = mode
        self._create_agent()
    
    def toggle_streaming(self):
        """切换流式输出"""
        self.streaming = not self.streaming
        status = "启用" if self.streaming else "禁用"
        self.command.stdout.write(f'{Colors.GREEN}✅ 流式输出已{status}{Colors.ENDC}')
    
    def toggle_tools(self):
        """切换工具"""
        self.use_tools = not self.use_tools
        self._create_agent()
        status = "启用" if self.use_tools else "禁用"
        self.command.stdout.write(f'{Colors.GREEN}✅ 工具已{status}{Colors.ENDC}')
    
    def clear_history(self):
        """清空对话历史"""
        self.chat_history = []
        self.command.stdout.write(f'{Colors.GREEN}✅ 对话历史已清空{Colors.ENDC}')
    
    def show_info(self):
        """显示当前配置"""
        info = f"""{Colors.CYAN}当前配置:{Colors.ENDC}
  模式: {Colors.YELLOW}{self.mode}{Colors.ENDC}
  流式输出: {Colors.YELLOW}{'是' if self.streaming else '否'}{Colors.ENDC}
  工具: {Colors.YELLOW}{'启用' if self.use_tools else '禁用'}{Colors.ENDC}
  对话历史: {Colors.YELLOW}{len(self.chat_history)} 条消息{Colors.ENDC}
"""
        self.command.stdout.write(info)
    
    def chat(self, message: str):
        """发送消息并获取回复"""
        if self.streaming:
            self.command.stdout.write(f'{Colors.BLUE}🤖 助手: {Colors.ENDC}', ending='')
            
            full_response = ""
            for chunk in self.agent.graph.stream(
                {"messages": [{"role": "user", "content": message}]},
                config={"recursion_limit": 50},
                stream_mode="messages"
            ):
                if hasattr(chunk, 'content') and chunk.content:
                    print(chunk.content, end='', flush=True)
                    full_response += chunk.content
            
            print()
            
            self.chat_history.append(HumanMessage(content=message))
            self.chat_history.append(AIMessage(content=full_response))
            
        else:
            response = self.agent.invoke(
                input_text=message,
                chat_history=self.chat_history,
            )
            
            self.command.stdout.write(f'{Colors.BLUE}🤖 助手: {response}{Colors.ENDC}')
            
            self.chat_history.append(HumanMessage(content=message))
            self.chat_history.append(AIMessage(content=response))

