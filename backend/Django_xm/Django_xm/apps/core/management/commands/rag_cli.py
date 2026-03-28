"""
RAG CLI 命令行工具
管理 RAG 索引和进行查询
"""

import sys
from pathlib import Path
from django.core.management.base import BaseCommand
from Django_xm.apps.rag.index_manager import IndexManager
from Django_xm.apps.rag.embeddings import get_embeddings
from Django_xm.apps.rag.loaders import load_documents_from_directory
from Django_xm.apps.rag.splitters import split_documents
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
    help = 'RAG 命令行工具 - 管理索引和查询'

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='action', help='可用操作')

        list_parser = subparsers.add_parser('list', help='列出所有索引')

        create_parser = subparsers.add_parser('create', help='创建新索引')
        create_parser.add_argument('name', type=str, help='索引名称')
        create_parser.add_argument('directory', type=str, help='文档目录路径')
        create_parser.add_argument('--description', '-d', type=str, default='', help='索引描述')
        create_parser.add_argument('--chunk-size', type=int, default=None, help='分块大小')
        create_parser.add_argument('--chunk-overlap', type=int, default=None, help='分块重叠')
        create_parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的索引')

        info_parser = subparsers.add_parser('info', help='查看索引信息')
        info_parser.add_argument('name', type=str, help='索引名称')

        delete_parser = subparsers.add_parser('delete', help='删除索引')
        delete_parser.add_argument('name', type=str, help='索引名称')

        query_parser = subparsers.add_parser('query', help='查询索引')
        query_parser.add_argument('name', type=str, help='索引名称')
        query_parser.add_argument('question', type=str, help='查询问题')

        search_parser = subparsers.add_parser('search', help='检索文档')
        search_parser.add_argument('name', type=str, help='索引名称')
        search_parser.add_argument('query', type=str, help='检索查询')

    def handle(self, *args, **options):
        action = options.get('action')

        if not action:
            self.stdout.write(self.style.ERROR('请指定操作: list, create, info, delete, query, search'))
            self.print_help()
            return

        manager = IndexManager()

        if action == 'list':
            self.list_indexes(manager)
        elif action == 'create':
            self.create_index(manager, options)
        elif action == 'info':
            self.index_info(manager, options['name'])
        elif action == 'delete':
            self.delete_index(manager, options['name'])
        elif action == 'query':
            self.query_index(manager, options['name'], options['question'])
        elif action == 'search':
            self.search_index(manager, options['name'], options['query'])

    def list_indexes(self, manager):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}索引列表{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}\n')

        try:
            indexes = manager.list_indexes()
            if not indexes:
                self.stdout.write(self.style.WARNING('没有找到索引'))
                return

            for idx in indexes:
                self.stdout.write(f'📦 {Colors.BOLD}{idx.get("name", "N/A")}{Colors.ENDC}')
                self.stdout.write(f'   描述: {idx.get("description", "N/A")}')
                self.stdout.write(f'   文档数: {idx.get("num_documents", "N/A")}')
                self.stdout.write(f'   创建时间: {idx.get("created_at", "N/A")}')
                self.stdout.write('')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 获取索引列表失败: {e}'))

    def create_index(self, manager, options):
        name = options['name']
        directory = options['directory']
        description = options.get('description', '')
        chunk_size = options.get('chunk_size')
        chunk_overlap = options.get('chunk_overlap')
        overwrite = options.get('overwrite', False)

        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}创建索引: {name}{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}\n')

        directory_path = Path(directory)
        if not directory_path.exists():
            self.stdout.write(self.style.ERROR(f'❌ 目录不存在: {directory}'))
            return

        try:
            if manager.index_exists(name) and not overwrite:
                self.stdout.write(self.style.ERROR(f'❌ 索引已存在: {name} (使用 --overwrite 覆盖)'))
                return

            self.stdout.write(f'{Colors.OKCYAN}📂 加载文档: {directory}{Colors.ENDC}')
            documents = load_documents_from_directory(str(directory_path))

            if not documents:
                self.stdout.write(self.style.ERROR('❌ 目录中没有找到支持的文档'))
                return

            self.stdout.write(f'{Colors.OKGREEN}✅ 加载了 {len(documents)} 个文档{Colors.ENDC}\n')

            self.stdout.write(f'{Colors.OKCYAN}✂️  分块文档...{Colors.ENDC}')
            chunks = split_documents(
                documents,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            self.stdout.write(f'{Colors.OKGREEN}✅ 生成了 {len(chunks)} 个文本块{Colors.ENDC}\n')

            self.stdout.write(f'{Colors.OKCYAN}🔢 创建 embeddings...{Colors.ENDC}')
            embeddings = get_embeddings()
            self.stdout.write(f'{Colors.OKGREEN}✅ Embeddings 创建成功{Colors.ENDC}\n')

            self.stdout.write(f'{Colors.OKCYAN}🗄️  创建向量索引...{Colors.ENDC}')
            vector_store = manager.create_index(
                name=name,
                documents=chunks,
                embeddings=embeddings,
                description=description,
                overwrite=overwrite,
            )
            self.stdout.write(f'{Colors.OKGREEN}✅ 索引创建成功: {name}{Colors.ENDC}\n')

            stats = manager.get_index_stats(name)
            self.stdout.write(f'{Colors.BOLD}索引统计:{Colors.ENDC}')
            self.stdout.write(f'   文档数: {stats.get("num_documents", "N/A")}')
            self.stdout.write(f'   向量维度: {stats.get("embedding_model", "N/A")}')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 创建索引失败: {e}'))
            import traceback
            traceback.print_exc()

    def index_info(self, manager, name):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}索引信息: {name}{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}\n')

        try:
            if not manager.index_exists(name):
                self.stdout.write(self.style.ERROR(f'❌ 索引不存在: {name}'))
                return

            stats = manager.get_index_stats(name)
            self.stdout.write(f'{Colors.BOLD}基本信息:{Colors.ENDC}')
            self.stdout.write(f'   名称: {stats.get("name", "N/A")}')
            self.stdout.write(f'   描述: {stats.get("description", "N/A")}')
            self.stdout.write(f'   向量存储类型: {stats.get("store_type", "N/A")}')
            self.stdout.write(f'   Embedding 模型: {stats.get("embedding_model", "N/A")}')
            self.stdout.write(f'   文档数: {stats.get("num_documents", "N/A")}')
            self.stdout.write(f'   创建时间: {stats.get("created_at", "N/A")}')
            self.stdout.write(f'   更新时间: {stats.get("updated_at", "N/A")}')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 获取索引信息失败: {e}'))

    def delete_index(self, manager, name):
        self.stdout.write(f'\n{Colors.WARNING}删除索引: {name}{Colors.ENDC}\n')

        try:
            if not manager.index_exists(name):
                self.stdout.write(self.style.ERROR(f'❌ 索引不存在: {name}'))
                return

            confirm = input('确定要删除此索引吗? (y/N): ')
            if confirm.lower() == 'y':
                manager.delete_index(name)
                self.stdout.write(self.style.SUCCESS(f'✅ 索引已删除: {name}'))
            else:
                self.stdout.write('取消删除')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 删除索引失败: {e}'))

    def query_index(self, manager, name, question):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}RAG 查询{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'📦 索引: {name}')
        self.stdout.write(f'🔍 问题: {question}\n')

        try:
            if not manager.index_exists(name):
                self.stdout.write(self.style.ERROR(f'❌ 索引不存在: {name}'))
                return

            self.stdout.write(f'{Colors.OKCYAN}加载索引...{Colors.ENDC}')
            embeddings = get_embeddings()
            vector_store = manager.load_index(name, embeddings)
            retriever = create_retriever(vector_store, k=4)
            agent = create_rag_agent(retriever)

            self.stdout.write(f'{Colors.OKCYAN}执行查询...{Colors.ENDC}\n')
            result = query_rag_agent(agent, question)

            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
            self.stdout.write(f'{Colors.BOLD}回答:{Colors.ENDC}')
            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
            self.stdout.write(result['answer'])
            self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}\n')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 查询失败: {e}'))
            import traceback
            traceback.print_exc()

    def search_index(self, manager, name, query):
        self.stdout.write(f'\n{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'{Colors.BOLD}文档检索{Colors.ENDC}')
        self.stdout.write(f'{Colors.HEADER}{"=" * 60}{Colors.ENDC}')
        self.stdout.write(f'📦 索引: {name}')
        self.stdout.write(f'🔍 查询: {query}\n')

        try:
            if not manager.index_exists(name):
                self.stdout.write(self.style.ERROR(f'❌ 索引不存在: {name}'))
                return

            self.stdout.write(f'{Colors.OKCYAN}加载索引...{Colors.ENDC}')
            embeddings = get_embeddings()
            vector_store = manager.load_index(name, embeddings)
            retriever = create_retriever(vector_store, k=5)

            self.stdout.write(f'{Colors.OKCYAN}执行检索...{Colors.ENDC}\n')
            docs = retriever.invoke(query)

            self.stdout.write(f'{Colors.OKGREEN}✅ 检索到 {len(docs)} 个相关文档{Colors.ENDC}\n')

            for i, doc in enumerate(docs, 1):
                self.stdout.write(f'{Colors.BOLD}文档 {i}:{Colors.ENDC}')
                content = doc.page_content[:300] + '...' if len(doc.page_content) > 300 else doc.page_content
                self.stdout.write(content)
                if doc.metadata:
                    self.stdout.write(f'   来源: {doc.metadata.get("source", "N/A")}')
                self.stdout.write('')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 检索失败: {e}'))
            import traceback
            traceback.print_exc()