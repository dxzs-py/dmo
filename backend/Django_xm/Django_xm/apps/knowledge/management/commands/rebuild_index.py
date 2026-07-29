"""
重建知识库向量索引的 Django management command

用途：当 embedding 模型切换导致维度不匹配时，使用指定 provider 重建索引。

示例：
    # 使用 Ollama bge-m3 重建指定索引
    python manage.py rebuild_index user_1_test1 --provider=ollama

    # 使用 OpenAI 重建
    python manage.py rebuild_index user_1_test1 --provider=openai

    # 列出所有索引及其维度信息
    python manage.py rebuild_index --list

    # 重建所有索引
    python manage.py rebuild_index --all --provider=ollama
"""

import time

from django.core.management.base import BaseCommand, CommandError

from Django_xm.apps.knowledge.config import (
    detect_embedding_dimension,
)
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.index_service import IndexManager


class Command(BaseCommand):
    help = "重建知识库向量索引（支持切换 embedding provider/模型以解决维度不匹配问题）"

    def add_arguments(self, parser):
        parser.add_argument(
            "index_name",
            nargs="?",
            type=str,
            help="要重建的索引名称（如 user_1_test1）",
        )
        parser.add_argument(
            "--provider",
            type=str,
            default=None,
            help="指定 embedding provider（openai/ollama/baidu_qianfan/local）",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="重建所有索引",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_indexes",
            help="列出所有索引及其维度信息",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help="仅显示将要执行的操作，不实际重建",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="文档嵌入批处理大小（默认 50）",
        )

    def handle(self, *args, **options):
        index_name: str | None = options.get("index_name")
        provider: str | None = options.get("provider")
        rebuild_all: bool = options.get("rebuild_all", False)
        list_indexes: bool = options.get("list_indexes", False)
        dry_run: bool = options.get("dry_run", False)
        batch_size: int = options.get("batch_size", 50)

        if list_indexes:
            self._list_indexes()
            return

        if not index_name and not rebuild_all:
            raise CommandError("请指定索引名称，或使用 --all 重建所有索引")

        if rebuild_all:
            self._rebuild_all(provider, dry_run, batch_size)
        else:
            self._rebuild_single(index_name, provider, dry_run, batch_size)

    def _list_indexes(self):
        """列出所有索引及其维度信息"""
        manager = IndexManager()
        indexes = manager.list_indexes()

        if not indexes:
            self.stdout.write("暂无索引")
            return

        self.stdout.write(f"{'索引名':<30} {'存储类型':<12} {'文档数':<8} {'Embedding模型':<25} {'维度':<8}")
        self.stdout.write("-" * 85)

        for idx in indexes:
            name = idx.get("name", "")
            store_type = idx.get("store_type", "")
            num_docs = idx.get("num_documents", 0)
            emb_model = idx.get("embedding_model", "")
            emb_dim = idx.get("embedding_dimension", "N/A")
            self.stdout.write(f"{name:<30} {store_type:<12} {num_docs:<8} {emb_model:<25} {emb_dim!s:<8}")

    def _rebuild_single(
        self,
        index_name: str,
        provider: str | None,
        dry_run: bool,
        batch_size: int,
    ):
        """重建单个索引"""
        manager = IndexManager()

        if not manager.index_exists(index_name):
            raise CommandError(f"索引不存在: {index_name}")

        metadata = manager._load_metadata(index_name) or {}
        old_model = metadata.get("embedding_model", "未知")
        old_dim = metadata.get("embedding_dimension", "未知")
        num_docs = metadata.get("num_documents", 0)

        self.stdout.write(f"索引: {index_name}")
        self.stdout.write(f"  当前 embedding: {old_model} (维度: {old_dim})")
        self.stdout.write(f"  文档数: {num_docs}")

        # 创建新 embedding
        embeddings = get_embeddings(
            preferred_provider=provider,
            use_cache=False,
        )

        # 探测新维度
        new_dim = detect_embedding_dimension(embeddings)
        self.stdout.write(f"  新 embedding 维度: {new_dim}")

        if dry_run:
            self.stdout.write(self.style.WARNING("  [DRY RUN] 未实际执行重建"))
            return

        if old_dim == new_dim:
            self.stdout.write(
                self.style.WARNING(f"  维度相同 ({old_dim})，无需重建。如需强制重建，请先删除索引再重新创建。")
            )
            return

        self.stdout.write(
            self.style.WARNING(f"  即将重建索引: 维度 {old_dim} -> {new_dim}，文档将被重新嵌入。此操作不可逆！")
        )

        confirm = input("确认继续？(y/N): ")
        if confirm.lower() != "y":
            self.stdout.write("已取消")
            return

        self._do_rebuild(manager, index_name, embeddings, batch_size)

    def _rebuild_all(
        self,
        provider: str | None,
        dry_run: bool,
        batch_size: int,
    ):
        """重建所有索引"""
        manager = IndexManager()
        indexes = manager.list_indexes()

        if not indexes:
            self.stdout.write("暂无索引")
            return

        self.stdout.write(f"将重建 {len(indexes)} 个索引:")

        embeddings = get_embeddings(
            preferred_provider=provider,
            use_cache=False,
        )
        new_dim = detect_embedding_dimension(embeddings)

        for idx in indexes:
            name = idx.get("name", "")
            old_dim = idx.get("embedding_dimension", "N/A")
            num_docs = idx.get("num_documents", 0)
            self.stdout.write(f"  {name}: 维度 {old_dim} -> {new_dim}, 文档数 {num_docs}")

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] 未实际执行重建"))
            return

        confirm = input("确认重建所有索引？(y/N): ")
        if confirm.lower() != "y":
            self.stdout.write("已取消")
            return

        for idx in indexes:
            name = idx.get("name", "")
            try:
                self._do_rebuild(manager, name, embeddings, batch_size)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"重建索引 {name} 失败: {e}"))

    def _do_rebuild(
        self,
        manager: IndexManager,
        index_name: str,
        embeddings,
        batch_size: int,
    ):
        """执行索引重建"""
        from langchain_core.documents import Document

        self.stdout.write(f"正在重建索引: {index_name}...")

        # 1. 读取旧索引中的文档
        try:
            old_store = manager.load_index(index_name, embeddings)
        except Exception:
            # 维度不匹配时无法加载，需要直接从 PGVector 读取原始文档
            self.stdout.write("  无法用新 embedding 加载旧索引，尝试从数据库直接读取文档...")
            old_store = None

        documents = []
        if old_store is not None:
            # 从旧向量库中提取文档
            try:
                if hasattr(old_store, "docstore") and hasattr(old_store, "index_to_docstore_id"):
                    for doc_id in old_store.index_to_docstore_id.values():
                        doc = old_store.docstore.search(doc_id)
                        if isinstance(doc, Document):
                            documents.append(doc)
                elif hasattr(old_store, "similarity_search"):
                    # PGVector: 用大 k 尝试获取所有文档
                    # 先获取文档总数
                    from django.db import connections

                    with connections["default"].cursor() as cursor:
                        cursor.execute(
                            """SELECT COUNT(*) FROM langchain_pg_embedding
                               WHERE collection_id = (
                                   SELECT uuid FROM langchain_pg_collection WHERE name = %s
                               )""",
                            [index_name],
                        )
                        total = cursor.fetchone()[0]

                    self.stdout.write(f"  从 PGVector 读取 {total} 条文档...")
                    if total > 0:
                        documents = old_store.similarity_search("", k=min(total, 10000))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  读取旧文档失败: {e}"))
                raise

        if not documents:
            self.stderr.write(
                self.style.ERROR(f"无法从索引 {index_name} 中读取文档。请确保原始文档文件仍然存在，然后重新上传。")
            )
            return

        self.stdout.write(f"  读取到 {len(documents)} 条文档")

        # 2. 用新 embedding 创建索引（overwrite=True 自动处理旧索引）
        store_type = manager._get_store_type(index_name)
        description = (manager._load_metadata(index_name) or {}).get("description", "")

        start_time = time.time()
        manager.create_index(
            name=index_name,
            documents=documents,
            embeddings=embeddings,
            description=description,
            store_type=store_type,
            overwrite=True,
        )
        elapsed = time.time() - start_time

        self.stdout.write(self.style.SUCCESS(f"  索引重建完成: {len(documents)} 条文档, 耗时 {elapsed:.1f}s"))
