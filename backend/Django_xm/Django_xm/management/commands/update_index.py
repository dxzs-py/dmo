"""
更新 RAG 索引的 Django management command
"""

from django.core.management.base import BaseCommand, CommandError
from pathlib import Path
from Django_xm.apps.rag import (
    IndexManager,
    load_directory,
    load_document,
    split_documents,
    get_embeddings,
    get_supported_extensions,
)
import json
from typing import Set, List
from datetime import datetime


class Command(BaseCommand):
    help = '更新 RAG 索引'

    def add_arguments(self, parser):
        parser.add_argument(
            'index_name',
            type=str,
            help='索引名称（如: test_index）'
        )
        parser.add_argument(
            'document_dir',
            type=str,
            help='文档目录（如: data/documents/test）'
        )
        parser.add_argument(
            '--rebuild',
            action='store_true',
            help='强制重建整个索引'
        )

    def handle(self, *args, **options):
        index_name = options['index_name']
        document_dir = options['document_dir']
        rebuild = options.get('rebuild', False)

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write('   📚 智能索引更新工具')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(f'索引名称: {index_name}')
        self.stdout.write(f'文档目录: {document_dir}')
        self.stdout.write(f'模式: {"重建" if rebuild else "增量更新"}')
        self.stdout.write(self.style.SUCCESS('=' * 60 + '\n'))

        try:
            updater = SmartIndexUpdater(index_name, document_dir)
            success = updater.update_index(rebuild=rebuild)

            if success:
                self.stdout.write(self.style.SUCCESS('\n✅ 更新完成！'))
                self.stdout.write('\n💡 下一步:')
                self.stdout.write(f'   测试查询: python manage.py test_rag {index_name} "你的问题"')
            else:
                self.stdout.write(self.style.ERROR('\n❌ 更新失败'))
                raise CommandError('索引更新失败')

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 错误：{str(e)}'))
            import traceback
            traceback.print_exc()
            raise CommandError(str(e))


class SmartIndexUpdater:
    """智能索引更新器"""
    
    def __init__(self, index_name: str, document_dir: str):
        self.index_name = index_name
        self.document_dir = Path(document_dir)
        self.manager = IndexManager()
        self.tracking_file = self.manager.base_path / index_name / "tracked_files.json"
        
    def get_tracked_files(self) -> Set[str]:
        """获取已跟踪的文件列表"""
        if not self.tracking_file.exists():
            return set()
        
        try:
            with open(self.tracking_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('files', []))
        except Exception as e:
            print(f"⚠️  读取跟踪文件失败: {e}")
            return set()
    
    def save_tracked_files(self, files: Set[str]):
        """保存已跟踪的文件列表"""
        self.tracking_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'files': sorted(list(files)),
            'last_updated': datetime.now().isoformat(),
            'total_files': len(files),
        }
        
        with open(self.tracking_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def get_all_document_files(self) -> Set[str]:
        """获取目录中所有支持的文档文件"""
        if not self.document_dir.exists():
            raise FileNotFoundError(f"文档目录不存在: {self.document_dir}")
        
        supported_exts = get_supported_extensions()
        all_files = set()
        
        for ext in supported_exts.keys():
            files = self.document_dir.glob(f"**/*{ext}")
            all_files.update(str(f.relative_to(self.document_dir)) for f in files)
        
        return all_files
    
    def find_new_files(self) -> List[str]:
        """查找新增的文档文件"""
        tracked = self.get_tracked_files()
        current = self.get_all_document_files()
        new_files = current - tracked
        return sorted(list(new_files))
    
    def update_index(self, rebuild: bool = False) -> bool:
        """更新索引"""
        # 检查索引是否存在
        if not self.manager.index_exists(self.index_name):
            print(f"❌ 索引不存在: {self.index_name}")
            print(f"\n💡 提示: 请先通过 API 创建索引")
            return False
        
        if rebuild:
            # 重建模式：处理所有文档
            print("🔄 重建模式：处理所有文档...\n")
            return self._rebuild_index()
        else:
            # 增量模式：只处理新文档
            print("➕ 增量模式：只处理新文档...\n")
            return self._incremental_update()
    
    def _rebuild_index(self) -> bool:
        """重建整个索引"""
        try:
            print("1️⃣  加载所有文档...")
            documents = load_directory(str(self.document_dir), show_progress=True)
            
            if not documents:
                print("⚠️  没有找到文档")
                return False
            
            print(f"✅ 加载了 {len(documents)} 个文档\n")
            
            print("2️⃣  分块文档...")
            chunks = split_documents(documents)
            print(f"✅ 生成了 {len(chunks)} 个文本块\n")
            
            print("3️⃣  创建 embeddings...")
            embeddings = get_embeddings()
            print("✅ Embeddings 准备完成\n")
            
            print("4️⃣  重建索引...")
            self.manager.create_index(
                name=self.index_name,
                documents=chunks,
                embeddings=embeddings,
                description=f"重建于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                overwrite=True,
            )
            print("✅ 索引重建成功\n")
            
            all_files = self.get_all_document_files()
            self.save_tracked_files(all_files)
            print(f"📝 已跟踪 {len(all_files)} 个文件\n")
            
            return True
            
        except Exception as e:
            print(f"\n❌ 重建索引失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _incremental_update(self) -> bool:
        """增量更新索引"""
        try:
            print("1️⃣  检测新文档...")
            new_files = self.find_new_files()
            
            if not new_files:
                print("✅ 没有新文档需要添加")
                print("\n💡 提示:")
                print("   - 所有文档都已索引")
                print("   - 如果要重建索引，使用: --rebuild")
                return True
            
            print(f"📄 发现 {len(new_files)} 个新文档:")
            for i, file in enumerate(new_files, 1):
                print(f"   {i}. {file}")
            print()
            
            print("2️⃣  加载新文档...")
            documents = []
            success_count = 0
            
            for file in new_files:
                file_path = self.document_dir / file
                try:
                    docs = load_document(str(file_path))
                    documents.extend(docs)
                    success_count += 1
                except Exception as e:
                    print(f"   ⚠️  加载失败: {file} - {e}")
            
            if not documents:
                print("❌ 没有成功加载任何文档")
                return False
            
            print(f"✅ 成功加载 {success_count}/{len(new_files)} 个文档\n")
            
            print("3️⃣  分块文档...")
            chunks = split_documents(documents)
            print(f"✅ 生成了 {len(chunks)} 个文本块\n")
            
            print("4️⃣  创建 embeddings...")
            embeddings = get_embeddings()
            print("✅ Embeddings 准备完成\n")
            
            print("5️⃣  更新索引...")
            self.manager.update_index(self.index_name, chunks, embeddings)
            print("✅ 索引更新成功\n")
            
            tracked = self.get_tracked_files()
            tracked.update(new_files)
            self.save_tracked_files(tracked)
            print(f"📝 已跟踪 {len(tracked)} 个文件（新增 {len(new_files)} 个）\n")
            
            info = self.manager.get_index_info(self.index_name)
            if info:
                print("📊 索引统计:")
                print(f"   总文档数: {info.get('num_documents', 'N/A')}")
                print(f"   更新时间: {info.get('updated_at', 'N/A')}")
                if 'size_mb' in info:
                    print(f"   索引大小: {info['size_mb']:.2f} MB")
            
            return True
            
        except Exception as e:
            print(f"\n❌ 更新索引失败: {e}")
            import traceback
            traceback.print_exc()
            return False
