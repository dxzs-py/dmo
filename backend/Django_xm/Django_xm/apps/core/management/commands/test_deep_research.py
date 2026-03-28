
"""
测试深度研究功能的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.deep_research.deep_agent import create_deep_research_agent


class Command(BaseCommand):
    help = '测试深度研究功能'

    def add_arguments(self, parser):
        parser.add_argument(
            '--query',
            type=str,
            required=True,
            help='研究问题'
        )
        parser.add_argument(
            '--thread-id',
            type=str,
            help='线程 ID（可选）'
        )
        parser.add_argument(
            '--enable-web-search',
            action='store_true',
            default=True,
            help='启用网络搜索'
        )
        parser.add_argument(
            '--enable-doc-analysis',
            action='store_true',
            default=False,
            help='启用文档分析'
        )

    def handle(self, *args, **options):
        query = options['query']
        thread_id = options.get('thread_id')
        enable_web_search = options.get('enable_web_search', True)
        enable_doc_analysis = options.get('enable_doc_analysis', False)

        self.stdout.write(self.style.SUCCESS('=== 深度研究测试 ==='))
        self.stdout.write(f'研究问题：{query}')
        self.stdout.write(f'网络搜索：{"启用" if enable_web_search else "禁用"}')
        self.stdout.write(f'文档分析：{"启用" if enable_doc_analysis else "禁用"}')
        if thread_id:
            self.stdout.write(f'线程 ID: {thread_id}')
        self.stdout.write('\n')

        try:
            # 创建深度研究 Agent
            self.stdout.write('正在创建深度研究 Agent...')
            agent = create_deep_research_agent(
                thread_id=thread_id,
                enable_web_search=enable_web_search,
                enable_doc_analysis=enable_doc_analysis,
            )
            
            # 执行研究
            self.stdout.write('正在执行深度研究...')
            self.stdout.write(self.style.WARNING('（这可能需要几分钟时间，请耐心等待）\n'))
            
            result = agent.research(query)
            
            # 显示结果
            self.stdout.write(self.style.SUCCESS('\n=== 深度研究完成 ===\n'))
            
            if result.get("error"):
                self.stdout.write(self.style.ERROR(f'错误：{result["error"]}'))
                return
            
            # 显示最终报告
            final_report = result.get('final_report')
            if final_report:
                self.stdout.write(self.style.SUCCESS('=== 最终报告 ==='))
                # 只显示前 2000 个字符，避免输出过长
                if len(final_report) > 2000:
                    self.stdout.write(final_report[:2000])
                    self.stdout.write(self.style.WARNING('\n...（报告过长，仅显示前 2000 字符）'))
                else:
                    self.stdout.write(final_report)
            
            # 显示研究计划
            plan = result.get('plan')
            if plan:
                self.stdout.write(self.style.SUCCESS('\n\n=== 研究计划 ==='))
                if isinstance(plan, dict):
                    self.stdout.write(f"目标：{plan.get('objective', 'N/A')}")
                    self.stdout.write(f"\n步骤：")
                    for i, step in enumerate(plan.get('steps', []), 1):
                        self.stdout.write(f"  {i}. {step}")
                else:
                    self.stdout.write(str(plan))
            
            # 显示来源
            sources = result.get('sources', [])
            if sources:
                self.stdout.write(self.style.SUCCESS('\n\n=== 来源 ==='))
                for i, source in enumerate(sources, 1):
                    if isinstance(source, dict):
                        self.stdout.write(f"{i}. {source.get('url', 'N/A')}")
                    else:
                        self.stdout.write(f"{i}. {source}")
            
            self.stdout.write(self.style.SUCCESS('\n\n✅ 深度研究测试成功完成'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 深度研究测试失败：{str(e)}'))
            import traceback
            traceback.print_exc()
            raise CommandError(str(e))
