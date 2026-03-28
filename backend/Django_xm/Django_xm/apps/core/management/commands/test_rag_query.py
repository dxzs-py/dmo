"""
测试 RAG 查询功能
验证 RAG Agent 是否正常工作
"""

from django.core.management.base import BaseCommand
from Django_xm.apps.rag.index_manager import IndexManager
from Django_xm.apps.rag.embeddings import get_embeddings
from Django_xm.apps.rag.retrievers import create_retriever
from Django_xm.apps.rag.rag_agent import create_rag_agent, query_rag_agent


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
    help = '测试 RAG 查询功能'

    def add_arguments(self, parser):
        parser.add_argument(
            '--index',
            type=str,
            default='test_index',
            help='索引名称 (默认: test_index)'
        )
        parser.add_argument(
            '--query',
            type=str,
            default='什么是机器学习？',
            help='查询问题'
        )

    def handle(self, *args, **options):
        index_name = options['index']
        query = options['query']

        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}RAG 查询测试{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'📝 索引: {index_name}')
        self.stdout.write(f'🔍 查询: {query}\n')

        try:
            manager = IndexManager()

            if not manager.index_exists(index_name):
                self.stdout.write(self.style.ERROR(f'❌ 索引不存在: {index_name}'))
                self.stdout.write(f'   请先创建索引:')
                self.stdout.write(f'   python manage.py test_rag --action create --index {index_name} --directory ./data/documents/test')
                return

            self.stdout.write('1️⃣  加载索引...')
            embeddings = get_embeddings()
            vector_store = manager.load_index(index_name, embeddings)
            self.stdout.write(self.style.SUCCESS('✅ 索引加载成功\n'))

            self.stdout.write('2️⃣  创建检索器...')
            retriever = create_retriever(vector_store, k=4)
            self.stdout.write(self.style.SUCCESS('✅ 检索器创建成功\n'))

            self.stdout.write('3️⃣  创建 RAG Agent...')
            agent = create_rag_agent(retriever)
            self.stdout.write(self.style.SUCCESS('✅ RAG Agent 创建成功\n'))

            self.stdout.write('4️⃣  执行查询...')
            result = query_rag_agent(agent, query)
            self.stdout.write(self.style.SUCCESS('✅ 查询完成\n'))

            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
            self.stdout.write(f'{Colors.BOLD}回答:{Colors.ENDC}')
            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
            self.stdout.write(result['answer'])
            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}\n')

            self.stdout.write(self.style.SUCCESS('✅ 测试成功！'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 测试失败: {e}'))
            import traceback
            traceback.print_exc()