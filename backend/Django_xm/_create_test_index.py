#!/usr/bin/env python
"""创建测试索引"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings.dev')
sys.path.insert(0, r'D:\Project\code\langchain_xm\backend\Django_xm')
django.setup()

from Django_xm.apps.rag.index_manager import IndexManager
from langchain_core.documents import Document
from Django_xm.apps.rag.embeddings import get_embeddings

print("=" * 80)
print("创建测试索引")
print("=" * 80)

# 创建测试文档
docs = [
    Document(
        page_content="LangChain 是一个用于开发由语言模型驱动的应用程序的框架。",
        metadata={"source": "langchain_docs"}
    ),
    Document(
        page_content="RAG (Retrieval-Augmented Generation) 是一种结合检索和生成的技术。",
        metadata={"source": "ai_tech_docs"}
    ),
    Document(
        page_content="FAISS 是 Facebook AI Similarity Search，用于高效的向量相似性搜索。",
        metadata={"source": "faiss_docs"}
    ),
]

# 创建索引
print("\n正在创建索引 'test_index'...")
manager = IndexManager()
embeddings = get_embeddings()

try:
    vector_store = manager.create_index(
        name="test_index",
        documents=docs,
        embeddings=embeddings,
        description="测试索引",
        overwrite=True,
    )
    print("✅ 索引创建成功！")
    
    # 验证索引
    print("\n验证索引...")
    exists = manager.index_exists("test_index")
    print(f"  index_exists: {exists}")
    
    indexes = manager.list_indexes()
    print(f"  现有索引: {[idx['name'] for idx in indexes]}")
    
    print("\n" + "=" * 80)
    print("完成！")
    print("=" * 80)
    
except Exception as e:
    print(f"❌ 创建索引失败: {e}")
    import traceback
    traceback.print_exc()
