
"""
更新 RAG 索引的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from pathlib import Path
from Django_xm.apps.rag.index_manager import IndexManager
from Django_xm.apps.rag.loaders import load_documents_from_directory
from Django_xm.apps.rag.embeddings import get_embeddings


class Command(BaseCommand):
    help = '更新 RAG 索引'

    def add_arguments(self, parser):
        parser.add_argument(
            'index_name',
            type=str,
            help='索引名称'
        )
        parser.add_argument(
            'documents_dir',
            type=str,
            help='文档目录路径'
        )
        parser.add_argument(
            '--rebuild',
            action='store_true',
            help='强制重建索引（删除并重新创建）'
        )

    def handle(self, *args, **options):
        index_name = options['index_name']
        documents_dir = options['documents_dir']
        rebuild = options.get('rebuild', False)

        self.stdout.write(self.style.SUCCESS('=== 更新 RAG 索引 ==='))
        self.stdout.write(f'索引名称：{index_name}')
        self.stdout.write(f'文档目录：{documents_dir}')
        self.stdout.write(f'重建模式：{"是" if rebuild else "否"}')
        self.stdout.write('\n')

        try:
            doc_path = Path(documents_dir)
            if not doc_path.exists():
                raise CommandError(f'文档目录不存在：{documents_dir}')
            
            self.stdout.write('正在加载文档...')
            documents = load_documents_from_directory(str(doc_path))
            self.stdout.write(self.style.SUCCESS(f'  成功加载 {len(documents)} 个文档块'))
            
            self.stdout.write('正在初始化 Embedding 模型...')
            embeddings = get_embeddings()
            
            self.stdout.write('正在初始化索引管理器...')
            index_manager = IndexManager()
            
            self.stdout.write('正在更新索引...')
            if rebuild:
                self.stdout.write('  删除现有索引...')
                index_manager.delete_index(index_name)
                self.stdout.write('  创建新索引...')
                index_manager.create_index(
                    index_name,
                    documents=documents,
                    embeddings=embeddings
                )
                self.stdout.write(self.style.SUCCESS(f'  成功创建索引，包含 {len(documents)} 个文档'))
            else:
                if not index_manager.index_exists(index_name):
                    self.stdout.write('  索引不存在，正在创建...')
                    index_manager.create_index(
                        index_name,
                        documents=documents,
                        embeddings=embeddings
                    )
                    self.stdout.write(self.style.SUCCESS(f'  成功创建索引，包含 {len(documents)} 个文档'))
                else:
                    self.stdout.write('  向现有索引添加文档...')
                    added_count = index_manager.add_documents(
                        index_name,
                        documents=documents,
                        embeddings=embeddings
                    )
                    self.stdout.write(self.style.SUCCESS(f'  成功添加 {added_count} 个文档'))
            
            self.stdout.write(self.style.SUCCESS('\n\n✅ 索引更新成功'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 索引更新失败：{str(e)}'))
            import traceback
            traceback.print_exc()
            raise CommandError(str(e))

