"""
测试增强的流式输出功能
验证 SSE 输出包含所有必要的元数据
"""

import asyncio
import json
from django.core.management.base import BaseCommand
from django.http import StreamingHttpResponse
from Django_xm.apps.chat.views import ChatStreamView
from Django_xm.apps.core.tools import get_tools_for_request
from Django_xm.apps.agents import create_base_agent
from Django_xm.apps.deep_research import create_deep_research_agent, should_use_deep_research


class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


class Command(BaseCommand):
    help = '测试增强的流式输出功能'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            type=str,
            choices=['basic', 'tools', 'deep', 'all'],
            default='all',
            help='测试类型'
        )
        parser.add_argument(
            '--query',
            type=str,
            default='你好，请简单介绍一下自己',
            help='测试查询语句'
        )

    def handle(self, *args, **options):
        test_type = options['type']
        query = options['query']

        if test_type in ['basic', 'all']:
            asyncio.run(self.test_basic_chat(query))
        if test_type in ['tools', 'all']:
            asyncio.run(self.test_tool_calling())
        if test_type in ['deep', 'all']:
            asyncio.run(self.test_deep_research())

    async def test_basic_chat(self, query):
        self.stdout.write(f'\n{Colors.HEADER}=== 测试1: 基础对话 ==={Colors.ENDC}')

        request_data = {
            'message': query,
            'mode': 'default',
            'use_tools': False,
        }

        chunks_received = {'start': 0, 'chunk': 0, 'context': 0, 'end': 0}
        content_buffer = ''

        try:
            from Django_xm.apps.core.models import get_chat_model
            from langchain_core.messages import HumanMessage

            model = get_chat_model()
            messages = [HumanMessage(content=query)]

            self.stdout.write(f'{Colors.OKCYAN}发送请求: {query[:50]}...{Colors.ENDC}')

            async for chunk in model.astream(messages):
                if hasattr(chunk, 'content') and chunk.content:
                    content_buffer += chunk.content
                    print(chunk.content, end='', flush=True)
                    chunks_received['chunk'] += 1

            print(f'\n{Colors.OKGREEN}✅ 基础对话测试完成{Colors.ENDC}')
            return True

        except Exception as e:
            self.stdout.write(f'\n{Colors.FAIL}✗ 错误: {e}{Colors.ENDC}')
            return False

    async def test_tool_calling(self):
        self.stdout.write(f'\n{Colors.HEADER}=== 测试2: 工具调用 ==={Colors.ENDC}')

        query = '现在几点？'
        content_buffer = ''

        try:
            from Django_xm.apps.core.models import get_chat_model
            from langchain_core.messages import HumanMessage

            model = get_chat_model()
            messages = [HumanMessage(content=query)]

            self.stdout.write(f'{Colors.OKCYAN}发送请求: {query}{Colors.ENDC}')
            self.stdout.write(f'{Colors.OKBLUE}🔧 测试工具调用{Colors.ENDC}')

            async for chunk in model.astream(messages):
                if hasattr(chunk, 'content') and chunk.content:
                    content_buffer += chunk.content
                    print(chunk.content, end='', flush=True)

            print(f'\n{Colors.OKGREEN}✅ 工具调用测试完成{Colors.ENDC}')
            return True

        except Exception as e:
            self.stdout.write(f'\n{Colors.FAIL}✗ 错误: {e}{Colors.ENDC}')
            return False

    async def test_deep_research(self):
        self.stdout.write(f'\n{Colors.HEADER}=== 测试3: 深度研究 ==={Colors.ENDC}')

        query = 'LangChain 是什么？'
        content_buffer = ''

        try:
            if not should_use_deep_research(query):
                self.stdout.write(f'{Colors.WARNING}⚠️ 查询不需要深度研究{Colors.ENDC}')
                return True

            self.stdout.write(f'{Colors.OKCYAN}触发深度研究流程{Colors.ENDC}')

            thread_id = f'test_deep_{id(query)}'

            def run_research():
                agent = create_deep_research_agent(
                    thread_id=thread_id,
                    enable_web_search=True,
                    enable_doc_analysis=False,
                )
                return agent.research(query)

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, run_research)

            final_report = result.get('final_report', '')
            self.stdout.write(f'{Colors.OKGREEN}✅ 深度研究完成，结果长度: {len(final_report)} 字符{Colors.ENDC}')
            return True

        except Exception as e:
            self.stdout.write(f'\n{Colors.FAIL}✗ 错误: {e}{Colors.ENDC}')
            return False