
"""
测试 RAG 功能的 Django management command
"""

import sys
from django.core.management.base import BaseCommand, CommandError
from Django_xm.apps.rag.index_manager import IndexManager
from Django_xm.apps.rag.embeddings import get_embeddings
from Django_xm.apps.rag.loaders import load_document, load_documents_from_directory
from Django_xm.apps.rag.splitters import split_documents
from Django_xm.apps.rag.retrievers import create_retriever
from Django_xm.apps.rag.rag_agent import create_rag_agent, query_rag_agent


class Command(BaseCommand):
    help = '测试 RAG 功能，包括索引创建、查询等'

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=str,
            choices=['list', 'create', 'query', 'stats', 'delete'],
            default='list',
            help='执行的操作类型'
        )
        parser.add_argument(
            '--name',
            type=str,
            help='索引名称'
        )
        parser.add_argument(
            '--directory',
            type=str,
            help='文档目录路径（用于创建索引）'
        )
        parser.add_argument(
            '--query',
            type=str,
            help='查询问题（用于查询索引）'
        )
        parser.add_argument(
            '--description',
            type=str,
            default='',
            help='索引描述'
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='覆盖已存在的索引'
        )

    def handle(self, *args, **options):
        action = options['action']
        name = options.get('name')
        directory = options.get('directory')
        query = options.get('query')
        description = options.get('description', '')
        overwrite = options.get('overwrite', False)

        manager = IndexManager()

        if action == 'list':
            self._list_indexes(manager)
        elif action == 'create':
            self._create_index(manager, name, directory, description, overwrite)
        elif action == 'query':
            self._query_index(manager, name, query)
        elif action == 'stats':
            self._get_stats(manager, name)
        elif action == 'delete':
            self._delete_index(manager, name)

    def _list_indexes(self, manager):
        """列出所有索引"""
        self.stdout.write(self.style.SUCCESS('正在获取索引列表...'))
        
        indexes = manager.list_indexes()
        
        if not indexes:
            self.stdout.write(self.style.WARNING('没有找到任何索引'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'找到 {len(indexes)} 个索引:'))
        for idx in indexes:
            self.stdout.write(f"\n  名称：{idx.get('name', 'N/A')}")
            self.stdout.write(f"  描述：{idx.get('description', 'N/A')}")
            self.stdout.write(f"  文档数：{idx.get('num_documents', 0)}")
            self.stdout.write(f"  创建时间：{idx.get('created_at', 'N/A')}")
            self.stdout.write(f"  更新时间：{idx.get('updated_at', 'N/A')}")

    def _create_index(self, manager, name, directory, description, overwrite):
        """创建索引"""
        if not name:
            raise CommandError('必须指定 --name 参数')
        
        if not directory:
            raise CommandError('必须指定 --directory 参数')
        
        self.stdout.write(self.style.SUCCESS(f'正在创建索引：{name}'))
        self.stdout.write(f'文档目录：{directory}')
        
        # 检查目录是否存在
        import os
        if not os.path.exists(directory):
            raise CommandError(f'目录不存在：{directory}')
        
        # 加载文档
        self.stdout.write('正在加载文档...')
        documents = load_documents_from_directory(directory)
        
        if not documents:
            raise CommandError('目录中没有找到支持的文档')
        
        self.stdout.write(self.style.SUCCESS(f'加载了 {len(documents)} 个文档'))
        
        # 分块文档
        self.stdout.write('正在分块文档...')
        chunks = split_documents(documents)
        self.stdout.write(self.style.SUCCESS(f'分块后得到 {len(chunks)} 个文档块'))
        
        # 创建 embeddings
        self.stdout.write('正在创建 embeddings...')
        embeddings = get_embeddings()
        
        # 创建索引
        self.stdout.write('正在创建向量索引...')
        try:
            vector_store = manager.create_index(
                name=name,
                documents=chunks,
                embeddings=embeddings,
                description=description,
                overwrite=overwrite,
            )
            self.stdout.write(self.style.SUCCESS(f'\n✅ 索引创建成功：{name}'))
        except Exception as e:
            raise CommandError(f'创建索引失败：{str(e)}')

    def _query_index(self, manager, name, query):
        """查询索引"""
        if not name:
            raise CommandError('必须指定 --name 参数')
        
        if not query:
            raise CommandError('必须指定 --query 参数')
        
        self.stdout.write(self.style.SUCCESS(f'正在查询索引：{name}'))
        self.stdout.write(f'查询问题：{query}\n')
        
        # 检查索引是否存在
        if not manager.index_exists(name):
            raise CommandError(f'索引不存在：{name}')
        
        # 加载索引
        embeddings = get_embeddings()
        vector_store = manager.load_index(name, embeddings)
        
        # 创建检索器
        retriever = create_retriever(vector_store, k=4)
        
        # 创建 RAG Agent
        agent = create_rag_agent(retriever)
        
        # 查询
        self.stdout.write('正在执行 RAG 查询...')
        result = query_rag_agent(agent, query, return_sources=True)
        
        self.stdout.write(self.style.SUCCESS('\n=== 回答 ==='))
        self.stdout.write(result.get('answer', 'N/A'))
        
        sources = result.get('sources', [])
        if sources:
            self.stdout.write(self.style.SUCCESS('\n=== 来源 ==='))
            for i, source in enumerate(sources, 1):
                self.stdout.write(f'{i}. {source}')

    def _get_stats(self, manager, name):
        """获取索引统计信息"""
        if not name:
            raise CommandError('必须指定 --name 参数')
        
        self.stdout.write(self.style.SUCCESS(f'正在获取索引统计：{name}'))
        
        # 检查索引是否存在
        if not manager.index_exists(name):
            raise CommandError(f'索引不存在：{name}')
        
        embeddings = get_embeddings()
        stats = manager.get_index_stats(name, embeddings)
        
        self.stdout.write('\n=== 索引统计 ===')
        for key, value in stats.items():
            self.stdout.write(f'{key}: {value}')

    def _delete_index(self, manager, name):
        """删除索引"""
        if not name:
            raise CommandError('必须指定 --name 参数')
        
        self.stdout.write(self.style.WARNING(f'正在删除索引：{name}'))
        
        # 检查索引是否存在
        if not manager.index_exists(name):
            raise CommandError(f'索引不存在：{name}')
        
        # 删除索引
        if manager.delete_index(name):
            self.stdout.write(self.style.SUCCESS(f'\n✅ 索引已删除：{name}'))
        else:
            raise CommandError('删除索引失败')
