"""
简单的模块验证脚本
用于测试核心模块是否能正确导入和初始化
"""
import os
import sys

# 添加 Django 项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置 Django 环境
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")

import django
django.setup()

print("=" * 60)
print("模块验证测试")
print("=" * 60)

# 1. 测试配置模块
print("\n1. 测试配置模块 (config)...")
try:
    from Django_xm.libs.langchain_core.config import settings, get_logger
    print("   ✅ 配置模块导入成功")
    print(f"   - OpenAI 模型: {settings.openai_model}")
    print(f"   - 温度: {settings.openai_temperature}")
    print(f"   - 流式输出: {settings.openai_streaming}")
except Exception as e:
    print(f"   ❌ 配置模块导入失败: {e}")

# 2. 测试模型模块
print("\n2. 测试模型模块 (models)...")
try:
    from Django_xm.libs.langchain_core.models import get_chat_model, get_streaming_model
    print("   ✅ 模型模块导入成功")
    print("   - get_chat_model 函数存在")
    print("   - get_streaming_model 函数存在")
except Exception as e:
    print(f"   ❌ 模型模块导入失败: {e}")

# 3. 测试提示词模块
print("\n3. 测试提示词模块 (prompts)...")
try:
    from Django_xm.libs.langchain_core.prompts import (
        get_system_prompt,
        get_prompt_with_tools,
        SYSTEM_PROMPTS,
        WRITER_GUIDELINES,
    )
    print("   ✅ 提示词模块导入成功")
    print(f"   - 系统提示词数量: {len(SYSTEM_PROMPTS)}")
    print(f"   - 可用模式: {list(SYSTEM_PROMPTS.keys())}")
except Exception as e:
    print(f"   ❌ 提示词模块导入失败: {e}")

# 4. 测试工具模块
print("\n4. 测试工具模块 (tools)...")
try:
    from Django_xm.libs.langchain_core.tools import (
        BASIC_TOOLS,
        get_tools_for_request,
    )
    print("   ✅ 工具模块导入成功")
    print(f"   - 基础工具数量: {len(BASIC_TOOLS)}")
    print(f"   - 工具名称: {[tool.name for tool in BASIC_TOOLS]}")
except Exception as e:
    print(f"   ❌ 工具模块导入失败: {e}")

# 5. 测试 Agents 模块
print("\n5. 测试 Agents 模块 (agents)...")
try:
    from Django_xm.libs.langchain_core.agents import BaseAgent, create_base_agent
    print("   ✅ Agents 模块导入成功")
    print("   - BaseAgent 类存在")
    print("   - create_base_agent 函数存在")
except Exception as e:
    print(f"   ❌ Agents 模块导入失败: {e}")

# 6. 测试 RAG 模块
print("\n6. 测试 RAG 模块 (rag_agent)...")
try:
    from Django_xm.libs.langchain_rag.rag_agent import create_rag_agent, DEFAULT_RAG_SYSTEM_PROMPT
    print("   ✅ RAG Agent 模块导入成功")
    print("   - create_rag_agent 函数存在")
    print("   - DEFAULT_RAG_SYSTEM_PROMPT 存在")
except Exception as e:
    print(f"   ❌ RAG Agent 模块导入失败: {e}")

print("\n7. 测试检索器模块 (retrievers)...")
try:
    from Django_xm.libs.langchain_rag.retrievers import create_retriever, create_retriever_tool
    print("   ✅ 检索器模块导入成功")
    print("   - create_retriever 函数存在")
    print("   - create_retriever_tool 函数存在")
except Exception as e:
    print(f"   ❌ 检索器模块导入失败: {e}")

# 8. 测试深度研究模块
print("\n8. 测试深度研究模块 (deep_research)...")
try:
    from Django_xm.libs.langchain_core.deep_research import (
        DeepResearchAgent,
        create_deep_research_agent,
        should_use_deep_research,
    )
    print("   ✅ 深度研究模块导入成功")
    print("   - DeepResearchAgent 类存在")
    print("   - create_deep_research_agent 函数存在")
    print("   - should_use_deep_research 函数存在")
    
    # 测试 should_use_deep_research
    test_queries = [
        "你好",
        "深度分析人工智能的发展趋势",
        "现在几点",
        "对比一下 React 和 Vue 的优缺点",
    ]
    print("   - 测试 should_use_deep_research:")
    for query in test_queries:
        result = should_use_deep_research(query)
        print(f"     '{query[:20]}...' -> {result}")
        
except Exception as e:
    print(f"   ❌ 深度研究模块导入失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("模块验证完成")
print("=" * 60)
