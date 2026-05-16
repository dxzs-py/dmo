"""
上下文管理系统测试

覆盖：
1. 上下文压缩引擎 - Token 估算、阈值检测、压缩策略
2. 知识图谱模块 - 实体提取、关系提取、图检索
3. 上下文管理器 - 统一接口、跨会话复用
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

from unittest import TestCase
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Any, List

from Django_xm.apps.context_manager.services.compression import (
    ContextCompressionEngine,
    CompressionConfig,
    CompressionResult,
    CompressionStrategy,
    TokenEstimator,
    EntityExtractor,
)
from Django_xm.apps.context_manager.services.knowledge_graph import (
    Entity,
    Relation,
    ConversationGraphExtractor,
    KnowledgeGraphStore,
    ContextKnowledgeGraph,
)
from Django_xm.apps.context_manager.services.manager import (
    ContextManager,
    ContextManagementConfig,
    create_context_manager,
)


# ==================== TokenEstimator 测试 ====================

class TokenEstimatorTest(TestCase):
    """Token 估算器测试"""

    def test_estimate_empty(self):
        self.assertEqual(TokenEstimator.estimate(""), 0)
        self.assertEqual(TokenEstimator.estimate(None), 0)

    def test_estimate_chinese(self):
        tokens = TokenEstimator.estimate("你好世界")
        self.assertGreater(tokens, 0)
        self.assertEqual(tokens, int(4 * 1.5))

    def test_estimate_english(self):
        tokens = TokenEstimator.estimate("hello world")
        self.assertGreater(tokens, 0)
        self.assertEqual(tokens, int(11 * 0.25))

    def test_estimate_mixed(self):
        tokens = TokenEstimator.estimate("你好hello")
        self.assertGreater(tokens, 0)

    def test_estimate_messages(self):
        from langchain_core.messages import HumanMessage, AIMessage
        messages = [
            HumanMessage(content="你好"),
            AIMessage(content="你好！有什么可以帮助你的吗？"),
        ]
        tokens = TokenEstimator.estimate_messages(messages)
        self.assertGreater(tokens, 0)

    def test_estimate_dict_messages(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        tokens = TokenEstimator.estimate_dict_messages(messages)
        self.assertGreater(tokens, 0)

    def test_get_model_limit_known(self):
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("claude-3-5-sonnet-20241022"), 200000)

    def test_get_model_limit_unknown(self):
        self.assertEqual(TokenEstimator.get_model_limit("unknown-model"), 128000)

    def test_get_model_limit_empty(self):
        self.assertEqual(TokenEstimator.get_model_limit(""), 128000)


# ==================== EntityExtractor 测试 ====================

class EntityExtractorTest(TestCase):
    """实体提取器测试"""

    def test_extract_from_empty(self):
        result = EntityExtractor.extract_from_messages([])
        self.assertEqual(result, [])

    def test_extract_tech_entities(self):
        messages = [
            {"role": "user", "content": "我想使用Django和Vue来开发项目"},
        ]
        result = EntityExtractor.extract_from_messages(messages)
        self.assertTrue(any("django" in e.lower() for e in result))
        self.assertTrue(any("vue" in e.lower() for e in result))

    def test_extract_quoted_entities(self):
        messages = [
            {"role": "user", "content": '项目叫做"智能助手"'},
        ]
        result = EntityExtractor.extract_from_messages(messages)
        self.assertTrue(any("智能助手" in e for e in result))

    def test_extract_preference_entities(self):
        messages = [
            {"role": "user", "content": "我喜欢Python编程"},
        ]
        result = EntityExtractor.extract_from_messages(messages)
        self.assertTrue(len(result) > 0)

    def test_deduplication(self):
        messages = [
            {"role": "user", "content": "使用Django开发"},
            {"role": "assistant", "content": "好的，使用Django是个好选择"},
            {"role": "user", "content": "Django怎么配置？"},
        ]
        result = EntityExtractor.extract_from_messages(messages)
        django_count = sum(1 for e in result if "django" in e.lower())
        self.assertLessEqual(django_count, 2)

    def test_max_entities_limit(self):
        messages = [{"role": "user", "content": f"项目{i}"} for i in range(50)]
        result = EntityExtractor.extract_from_messages(messages)
        self.assertLessEqual(len(result), 30)


# ==================== ContextCompressionEngine 测试 ====================

class CompressionConfigTest(TestCase):
    """压缩配置测试"""

    def test_default_config(self):
        config = CompressionConfig()
        self.assertEqual(config.token_threshold_ratio, 0.8)
        self.assertEqual(config.max_context_tokens, 128000)
        self.assertEqual(config.keep_recent_messages, 6)
        self.assertEqual(config.strategy, CompressionStrategy.HYBRID)

    def test_trigger_threshold(self):
        config = CompressionConfig(max_context_tokens=100000, token_threshold_ratio=0.8)
        self.assertEqual(config.trigger_threshold, 80000)

    def test_custom_config(self):
        config = CompressionConfig(
            max_context_tokens=200000,
            token_threshold_ratio=0.7,
            keep_recent_messages=10,
            strategy=CompressionStrategy.SUMMARY,
        )
        self.assertEqual(config.trigger_threshold, 140000)
        self.assertEqual(config.strategy, CompressionStrategy.SUMMARY)


class ContextCompressionEngineTest(TestCase):
    """上下文压缩引擎测试"""

    def _make_long_messages(self, count: int = 50, content_size: int = 200) -> List[Dict[str, Any]]:
        messages = [{"role": "system", "content": "你是助手"}]
        for i in range(count):
            messages.append({"role": "user", "content": f"用户消息{i}：" + "测试" * content_size})
            messages.append({"role": "assistant", "content": f"助手回复{i}：" + "回答" * content_size})
        return messages

    def test_should_not_compress_short(self):
        config = CompressionConfig(max_context_tokens=100000, token_threshold_ratio=0.8)
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        self.assertFalse(engine.should_compress(messages))

    def test_should_compress_long(self):
        config = CompressionConfig(max_context_tokens=1000, token_threshold_ratio=0.8)
        engine = ContextCompressionEngine(config)
        messages = self._make_long_messages(20, 100)
        self.assertTrue(engine.should_compress(messages))

    def test_compress_not_needed(self):
        config = CompressionConfig(max_context_tokens=100000)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "你好"}]
        result_messages, result = engine.compress(messages)
        self.assertFalse(result.compressed)
        self.assertEqual(len(result_messages), len(messages))

    def test_compress_sliding_window(self):
        config = CompressionConfig(
            max_context_tokens=500,
            token_threshold_ratio=0.5,
            keep_recent_messages=4,
            strategy=CompressionStrategy.SLIDING_WINDOW,
        )
        engine = ContextCompressionEngine(config)
        messages = self._make_long_messages(10, 50)
        result_messages, result = engine.compress(messages)
        self.assertTrue(result.compressed)
        self.assertEqual(result.strategy_used, CompressionStrategy.SLIDING_WINDOW)
        self.assertLessEqual(len(result_messages), config.keep_recent_messages + 2)

    def test_compress_preserves_system(self):
        config = CompressionConfig(
            max_context_tokens=500,
            token_threshold_ratio=0.5,
            keep_recent_messages=4,
            strategy=CompressionStrategy.SLIDING_WINDOW,
            preserve_system_messages=True,
        )
        engine = ContextCompressionEngine(config)
        messages = self._make_long_messages(10, 50)
        result_messages, result = engine.compress(messages)
        system_msgs = [m for m in result_messages if m.get('role') == 'system']
        self.assertTrue(len(system_msgs) >= 1)

    def test_compression_result_fields(self):
        config = CompressionConfig(
            max_context_tokens=500,
            token_threshold_ratio=0.5,
            keep_recent_messages=4,
            strategy=CompressionStrategy.HYBRID,
        )
        engine = ContextCompressionEngine(config)
        messages = self._make_long_messages(10, 50)
        result_messages, result = engine.compress(messages)
        self.assertTrue(result.compressed)
        self.assertGreater(result.original_token_estimate, 0)
        self.assertIsNotNone(result.strategy_used)

    @patch("Django_xm.apps.context_manager.services.compression.ContextCompressionEngine._generate_summary")
    def test_compress_hybrid_with_summary(self, mock_summary):
        mock_summary.return_value = "对话摘要：讨论了项目开发"
        config = CompressionConfig(
            max_context_tokens=500,
            token_threshold_ratio=0.5,
            keep_recent_messages=4,
            strategy=CompressionStrategy.HYBRID,
        )
        engine = ContextCompressionEngine(config)
        messages = self._make_long_messages(10, 50)
        result_messages, result = engine.compress(messages)
        self.assertTrue(result.compressed)
        self.assertEqual(result.strategy_used, CompressionStrategy.HYBRID)
        self.assertEqual(result.summary, "对话摘要：讨论了项目开发")

    def test_fallback_summary(self):
        messages = [
            {"role": "user", "content": "如何配置Django？"},
            {"role": "assistant", "content": "首先安装Django..."},
            {"role": "user", "content": "如何配置数据库？"},
        ]
        summary = ContextCompressionEngine._fallback_summary(messages)
        self.assertIn("Django", summary)


# ==================== KnowledgeGraph 测试 ====================

class EntityTest(TestCase):
    """Entity 数据类测试"""

    def test_to_dict_and_from_dict(self):
        entity = Entity(
            name="Django",
            entity_type="technology",
            confidence=0.9,
            mention_count=3,
        )
        data = entity.to_dict()
        restored = Entity.from_dict(data)
        self.assertEqual(restored.name, "Django")
        self.assertEqual(restored.entity_type, "technology")
        self.assertEqual(restored.confidence, 0.9)
        self.assertEqual(restored.mention_count, 3)


class RelationTest(TestCase):
    """Relation 数据类测试"""

    def test_key_generation(self):
        rel = Relation(source="Django", target="Python", relation_type="uses")
        self.assertEqual(rel.key, "Django|uses|Python")

    def test_to_dict_and_from_dict(self):
        rel = Relation(source="Django", target="Python", relation_type="uses", confidence=0.8)
        data = rel.to_dict()
        restored = Relation.from_dict(data)
        self.assertEqual(restored.source, "Django")
        self.assertEqual(restored.target, "Python")
        self.assertEqual(restored.relation_type, "uses")


class ConversationGraphExtractorTest(TestCase):
    """对话图提取器测试"""

    def test_extract_empty(self):
        extractor = ConversationGraphExtractor()
        entities, relations = extractor.extract([])
        self.assertEqual(len(entities), 0)
        self.assertEqual(len(relations), 0)

    def test_extract_tech_entities(self):
        extractor = ConversationGraphExtractor()
        messages = [
            {"role": "user", "content": "我想用Django和LangChain开发"},
        ]
        entities, relations = extractor.extract(messages)
        entity_names = [e.name for e in entities]
        self.assertTrue(any("django" in n.lower() for n in entity_names))
        self.assertTrue(any("langchain" in n.lower() for n in entity_names))

    def test_extract_quoted_entities(self):
        extractor = ConversationGraphExtractor()
        messages = [
            {"role": "user", "content": '项目叫"智能助手"'},
        ]
        entities, _ = extractor.extract(messages)
        self.assertTrue(any("智能助手" in e.name for e in entities))

    def test_extract_updates_mention_count(self):
        extractor = ConversationGraphExtractor()
        messages = [
            {"role": "user", "content": "使用Django开发"},
            {"role": "assistant", "content": "Django是一个好框架"},
            {"role": "user", "content": "Django怎么配置？"},
        ]
        entities, _ = extractor.extract(messages)
        django_entity = next((e for e in entities if "django" in e.name.lower()), None)
        self.assertIsNotNone(django_entity)
        self.assertGreater(django_entity.mention_count, 1)

    def test_extract_preference_entities(self):
        extractor = ConversationGraphExtractor()
        messages = [
            {"role": "user", "content": "我喜欢Python编程"},
        ]
        entities, _ = extractor.extract(messages)
        pref_entities = [e for e in entities if e.entity_type == "preference"]
        self.assertTrue(len(pref_entities) > 0)


class KnowledgeGraphStoreTest(TestCase):
    """知识图谱存储测试"""

    def test_save_and_load_local(self):
        store = KnowledgeGraphStore(store=None)
        entities = [Entity(name="Django", entity_type="technology")]
        relations = [Relation(source="Django", target="Python", relation_type="uses")]

        store.save_graph(user_id=1, entities=entities, relations=relations)
        loaded_entities, loaded_relations = store.load_graph(user_id=1)

        self.assertEqual(len(loaded_entities), 1)
        self.assertEqual(len(loaded_relations), 1)
        self.assertEqual(loaded_entities[0].name, "Django")

    def test_merge_graph(self):
        store = KnowledgeGraphStore(store=None)
        e1 = [Entity(name="Django", entity_type="technology", mention_count=1)]
        r1 = [Relation(source="Django", target="Python", relation_type="uses")]

        store.merge_graph(user_id=1, new_entities=e1, new_relations=r1)

        e2 = [Entity(name="Django", entity_type="technology", mention_count=1)]
        r2 = [Relation(source="Django", target="Vue", relation_type="uses")]

        merged_e, merged_r = store.merge_graph(user_id=1, new_entities=e2, new_relations=r2)

        django_entity = next(e for e in merged_e if e.name == "Django")
        self.assertGreater(django_entity.mention_count, 1)
        self.assertEqual(len(merged_r), 2)

    def test_get_related_context(self):
        store = KnowledgeGraphStore(store=None)
        entities = [
            Entity(name="Django", entity_type="technology"),
            Entity(name="Python", entity_type="technology"),
        ]
        relations = [Relation(source="Django", target="Python", relation_type="uses")]

        store.save_graph(user_id=1, entities=entities, relations=relations)
        context = store.get_related_context(user_id=1, query="Django配置")

        self.assertIn("Django", context)
        self.assertIn("Python", context)

    def test_expand_entities(self):
        relations = [
            Relation(source="A", target="B", relation_type="related_to"),
            Relation(source="B", target="C", relation_type="related_to"),
            Relation(source="C", target="D", relation_type="related_to"),
        ]
        expanded = KnowledgeGraphStore._expand_entities({"A"}, relations, max_hops=2)
        self.assertIn("A", expanded)
        self.assertIn("B", expanded)
        self.assertIn("C", expanded)
        self.assertNotIn("D", expanded)

    def test_format_context(self):
        entities = [Entity(name="Django", entity_type="technology", mention_count=3)]
        relations = [Relation(source="Django", target="Python", relation_type="uses")]
        context = KnowledgeGraphStore._format_context(entities, relations)
        self.assertIn("知识图谱上下文", context)
        self.assertIn("Django", context)

    def test_format_context_empty(self):
        context = KnowledgeGraphStore._format_context([], [])
        self.assertEqual(context, "")


class ContextKnowledgeGraphTest(TestCase):
    """知识图谱上下文管理器测试"""

    def test_process_conversation(self):
        kg = ContextKnowledgeGraph(store=None)
        messages = [
            {"role": "user", "content": "我想用Django开发项目"},
            {"role": "assistant", "content": "好的，Django是一个优秀的Web框架"},
        ]
        entities, relations = kg.process_conversation(user_id=1, messages=messages)
        self.assertTrue(len(entities) > 0)

    def test_get_context_for_query(self):
        kg = ContextKnowledgeGraph(store=None)
        messages = [
            {"role": "user", "content": "使用Django开发Web应用"},
        ]
        kg.process_conversation(user_id=1, messages=messages)
        context = kg.get_context_for_query(user_id=1, query="Django配置")
        self.assertTrue(len(context) > 0)

    def test_clear_graph(self):
        kg = ContextKnowledgeGraph(store=None)
        messages = [{"role": "user", "content": "使用Django"}]
        kg.process_conversation(user_id=1, messages=messages)
        result = kg.clear_graph(user_id=1)
        self.assertTrue(result)
        entities, relations = kg.get_full_graph(user_id=1)
        self.assertEqual(len(entities), 0)


# ==================== ContextManager 测试 ====================

class ContextManagementConfigTest(TestCase):
    """上下文管理配置测试"""

    def test_default_config(self):
        config = ContextManagementConfig()
        self.assertTrue(config.compression_enabled)
        self.assertTrue(config.knowledge_graph_enabled)
        self.assertTrue(config.cross_session_enabled)
        self.assertEqual(config.compression_strategy, "hybrid")

    def test_from_settings(self):
        config = ContextManagementConfig.from_settings()
        self.assertIsNotNone(config)


class ContextManagerTest(TestCase):
    """上下文管理器统一接口测试"""

    def _make_long_messages(self, count: int = 20) -> List[Dict[str, Any]]:
        messages = []
        for i in range(count):
            messages.append({"role": "user", "content": f"用户消息{i}：" + "测试" * 100})
            messages.append({"role": "assistant", "content": f"助手回复{i}：" + "回答" * 100})
        return messages

    def test_init_default(self):
        mgr = ContextManager(user_id=1, store=None)
        self.assertIsNotNone(mgr._compression_engine)
        self.assertIsNotNone(mgr._knowledge_graph)

    def test_init_compression_disabled(self):
        config = ContextManagementConfig(compression_enabled=False)
        mgr = ContextManager(user_id=1, config=config, store=None)
        self.assertIsNone(mgr._compression_engine)

    def test_init_kg_disabled(self):
        config = ContextManagementConfig(knowledge_graph_enabled=False)
        mgr = ContextManager(user_id=1, config=config, store=None)
        self.assertIsNone(mgr._knowledge_graph)

    def test_process_messages_short(self):
        config = ContextManagementConfig(
            compression_enabled=True,
            knowledge_graph_enabled=False,
        )
        mgr = ContextManager(user_id=1, config=config, store=None)
        messages = [{"role": "user", "content": "你好"}]
        result_messages, metadata = mgr.process_messages(messages)
        self.assertFalse(metadata["compression"]["compressed"])

    def test_process_messages_long(self):
        config = ContextManagementConfig(
            compression_enabled=True,
            knowledge_graph_enabled=False,
            compression_threshold_ratio=0.01,
        )
        mgr = ContextManager(user_id=1, config=config, store=None)
        messages = self._make_long_messages(20)
        result_messages, metadata = mgr.process_messages(messages)
        self.assertTrue(metadata["compression"]["compressed"])
        self.assertGreater(metadata["compression"]["original_tokens"], 0)

    def test_process_messages_with_kg(self):
        config = ContextManagementConfig(
            compression_enabled=False,
            knowledge_graph_enabled=True,
        )
        mgr = ContextManager(user_id=1, config=config, store=None)
        messages = [
            {"role": "user", "content": "使用Django开发"},
            {"role": "assistant", "content": "Django是Web框架"},
        ]
        result_messages, metadata = mgr.process_messages(messages)
        self.assertIsNotNone(metadata["knowledge_graph"])
        self.assertGreater(metadata["knowledge_graph"]["entity_count"], 0)

    def test_get_injection_context(self):
        mgr = ContextManager(user_id=1, store=None)
        messages = [
            {"role": "user", "content": "使用Django开发项目"},
        ]
        mgr.process_messages(messages)
        context = mgr.get_injection_context(query="Django配置")
        self.assertTrue(len(context) > 0)

    def test_get_injection_context_no_user(self):
        mgr = ContextManager(user_id=None, store=None)
        context = mgr.get_injection_context(query="Django")
        self.assertEqual(context, "")

    def test_get_stats(self):
        mgr = ContextManager(user_id=1, store=None)
        stats = mgr.get_stats()
        self.assertEqual(stats["user_id"], 1)
        self.assertTrue(stats["compression_enabled"])
        self.assertTrue(stats["knowledge_graph_enabled"])

    def test_compute_relevance(self):
        score = ContextManager._compute_relevance(
            query="Django配置",
            summary="讨论了Django的安装和配置",
            entities=["Django"],
        )
        self.assertGreater(score, 0)

    def test_compute_relevance_no_overlap(self):
        score = ContextManager._compute_relevance(
            query="天气查询",
            summary="讨论了Django开发",
            entities=["Django"],
        )
        self.assertLess(score, 0.5)


class CreateContextManagerTest(TestCase):
    """工厂函数测试"""

    def test_create_context_manager(self):
        mgr = create_context_manager(user_id=1, store=None)
        self.assertIsNotNone(mgr)
        self.assertEqual(mgr.user_id, 1)


# ==================== 集成测试 ====================

class ContextManagementIntegrationTest(TestCase):
    """上下文管理集成测试"""

    def test_full_pipeline(self):
        config = ContextManagementConfig(
            compression_enabled=True,
            knowledge_graph_enabled=True,
            cross_session_enabled=False,
            compression_threshold_ratio=0.01,
        )
        mgr = ContextManager(user_id=999, config=config, store=None)

        messages = []
        for i in range(30):
            messages.append({"role": "user", "content": f"第{i}条消息：" + "测试内容" * 50})
            messages.append({"role": "assistant", "content": f"第{i}条回复：" + "回答内容" * 50})

        result_messages, metadata = mgr.process_messages(messages)

        self.assertTrue(metadata["compression"]["compressed"])
        self.assertLess(
            metadata["compression"]["compressed_tokens"],
            metadata["compression"]["original_tokens"],
        )

        self.assertLessEqual(len(result_messages), len(messages))

        system_msgs = [m for m in result_messages if m.get("role") == "system"]
        self.assertTrue(len(system_msgs) >= 1)

    def test_kg_context_injection(self):
        mgr = ContextManager(user_id=998, store=None)

        messages = [
            {"role": "user", "content": "使用Django和Python开发Web应用"},
            {"role": "assistant", "content": "Django是基于Python的Web框架"},
            {"role": "user", "content": "如何配置Django的数据库？"},
            {"role": "assistant", "content": "可以在settings.py中配置DATABASES"},
        ]
        mgr.process_messages(messages)

        context = mgr.get_injection_context(query="Django数据库配置")
        self.assertTrue(len(context) > 0)

    def test_multi_session_kg_persistence(self):
        kg = ContextKnowledgeGraph(store=None)

        messages1 = [
            {"role": "user", "content": "使用Django开发"},
        ]
        kg.process_conversation(user_id=997, messages=messages1)

        messages2 = [
            {"role": "user", "content": "使用Vue做前端"},
        ]
        entities, relations = kg.process_conversation(user_id=997, messages=messages2)

        entity_names = [e.name for e in entities]
        self.assertTrue(any("django" in n.lower() for n in entity_names))
        self.assertTrue(any("vue" in n.lower() for n in entity_names))

    def test_compression_preserves_semantic_integrity(self):
        config = CompressionConfig(
            max_context_tokens=500,
            token_threshold_ratio=0.3,
            keep_recent_messages=4,
            strategy=CompressionStrategy.HYBRID,
            entity_aware=True,
        )
        engine = ContextCompressionEngine(config)

        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "项目叫做Alpha，使用Django框架"},
            {"role": "assistant", "content": "好的，Alpha项目将使用Django"},
            {"role": "user", "content": "数据库选择PostgreSQL"},
            {"role": "assistant", "content": "PostgreSQL是好的选择"},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"后续问题{i}：" + "内容" * 50})
            messages.append({"role": "assistant", "content": f"后续回答{i}：" + "内容" * 50})

        result_messages, result = engine.compress(messages)

        self.assertTrue(result.compressed)
        self.assertTrue(len(result.key_entities) > 0)

        system_msgs = [m for m in result_messages if m.get("role") == "system"]
        combined_content = " ".join(m.get("content", "") for m in system_msgs)
        self.assertTrue(
            any(keyword in combined_content for keyword in ["Alpha", "Django", "PostgreSQL", "摘要", "实体"]),
            f"压缩后系统消息中缺少关键信息: {combined_content[:200]}"
        )
