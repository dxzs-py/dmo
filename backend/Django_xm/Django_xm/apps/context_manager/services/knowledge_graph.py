"""
知识图谱上下文管理模块

构建实体关系网络来结构化存储和检索对话中的关键信息，
实现上下文的智能关联与高效检索，支持跨会话复用。

核心能力：
1. 从对话中自动提取实体和关系
2. 构建/维护实体关系图
3. 基于图结构检索相关上下文
4. 持久化到 LangGraph Store 实现跨会话复用
5. 可配置的实体类型和关系类型

参考：
- https://docs.langchain.com/oss/python/langchain/graph
- https://docs.langchain.com/oss/python/langgraph/persistence#memory-store
"""

import json
import time
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from Django_xm.apps.context_manager.config import get_logger

logger = get_logger(__name__)


@dataclass
class Entity:
    name: str
    entity_type: str = "concept"
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    first_seen: float = 0.0
    last_seen: float = 0.0
    mention_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": self.properties,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "mention_count": self.mention_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Entity":
        return cls(
            name=data["name"],
            entity_type=data.get("entity_type", "concept"),
            properties=data.get("properties", {}),
            confidence=data.get("confidence", 1.0),
            first_seen=data.get("first_seen", 0.0),
            last_seen=data.get("last_seen", 0.0),
            mention_count=data.get("mention_count", 1),
        )


@dataclass
class Relation:
    source: str
    target: str
    relation_type: str = "related_to"
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    created_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.source}|{self.relation_type}|{self.target}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation_type": self.relation_type,
            "properties": self.properties,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Relation":
        return cls(
            source=data["source"],
            target=data["target"],
            relation_type=data.get("relation_type", "related_to"),
            properties=data.get("properties", {}),
            confidence=data.get("confidence", 1.0),
            created_at=data.get("created_at", 0.0),
        )


ENTITY_TYPES = {
    "person", "organization", "location", "technology",
    "concept", "document", "project", "task", "preference", "event",
}

RELATION_TYPES = {
    "related_to", "part_of", "depends_on", "uses", "created_by",
    "belongs_to", "prefers", "mentions", "solves", "contradicts",
}


class ConversationGraphExtractor:
    """从对话中提取实体和关系"""

    _TECH_KEYWORDS = frozenset({
        "python", "django", "vue", "react", "langchain", "langgraph",
        "docker", "kubernetes", "api", "rest", "graphql", "sql",
        "postgresql", "redis", "chroma", "faiss", "openai", "claude",
        "gpt", "llm", "agent", "rag", "embedding", "vector",
    })

    _ACTION_PATTERNS = [
        ("prefers", ["喜欢", "偏好", "倾向于", "prefer", "like", "want"]),
        ("uses", ["使用", "用", "采用", "use", "using", "with"]),
        ("depends_on", ["依赖", "需要", "取决于", "depend", "require", "need"]),
        ("part_of", ["属于", "包含", "部分", "part of", "belong to", "include"]),
        ("solves", ["解决", "修复", "处理", "solve", "fix", "handle"]),
    ]

    def extract(self, messages: List[Dict[str, Any]]) -> Tuple[List[Entity], List[Relation]]:
        now = time.time()
        entities: Dict[str, Entity] = {}
        relations: Dict[str, Relation] = {}

        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    block.get('text', '') if isinstance(block, dict) else str(block)
                    for block in content
                )
            if not isinstance(content, str) or not content.strip():
                continue

            msg_entities = self._extract_entities(content, role, now)
            for entity in msg_entities:
                if entity.name in entities:
                    entities[entity.name].mention_count += 1
                    entities[entity.name].last_seen = now
                    entities[entity.name].confidence = min(1.0, entities[entity.name].confidence + 0.1)
                else:
                    entities[entity.name] = entity

            msg_relations = self._extract_relations(content, list(entities.keys()))
            for rel in msg_relations:
                if rel.key not in relations:
                    rel.created_at = now
                    relations[rel.key] = rel

        return list(entities.values()), list(relations.values())

    def _extract_entities(self, text: str, role: str, timestamp: float) -> List[Entity]:
        import re
        entities = []

        for tech in self._TECH_KEYWORDS:
            if tech.lower() in text.lower():
                entities.append(Entity(
                    name=tech,
                    entity_type="technology",
                    confidence=0.9 if role == "user" else 0.7,
                    first_seen=timestamp,
                    last_seen=timestamp,
                ))

        quoted = re.findall(r'[""「」『』]([^""「」『』]{2,40})[""「」『』]', text)
        for q in quoted:
            entities.append(Entity(
                name=q,
                entity_type="concept",
                confidence=0.8,
                first_seen=timestamp,
                last_seen=timestamp,
            ))

        name_patterns = [
            r'(?:叫做?|名为|称为|名字是)\s*([^\s，。！？,.!?]{2,20})',
            r'(?:项目|任务|文档|文件)\s*[""「」]([^""「」]{2,30})[""「」]',
        ]
        for pattern in name_patterns:
            matches = re.findall(pattern, text)
            for m in matches:
                entities.append(Entity(
                    name=m,
                    entity_type="concept",
                    confidence=0.85,
                    first_seen=timestamp,
                    last_seen=timestamp,
                ))

        if role == "user":
            pref_patterns = [
                r'(?:我喜欢|我偏好|我想要|我需要)\s*([^\s，。！？,.!?]{2,30})',
            ]
            for pattern in pref_patterns:
                matches = re.findall(pattern, text)
                for m in matches:
                    entities.append(Entity(
                        name=m,
                        entity_type="preference",
                        confidence=0.9,
                        first_seen=timestamp,
                        last_seen=timestamp,
                    ))

        return entities

    def _extract_relations(self, text: str, known_entities: List[str]) -> List[Relation]:
        relations = []
        text_lower = text.lower()

        for rel_type, keywords in self._ACTION_PATTERNS:
            for keyword in keywords:
                if keyword in text_lower:
                    parts = text_lower.split(keyword, 1)
                    if len(parts) == 2:
                        source = self._find_nearest_entity(parts[0], known_entities)
                        target = self._find_nearest_entity(parts[1], known_entities)
                        if source and target and source != target:
                            relations.append(Relation(
                                source=source,
                                target=target,
                                relation_type=rel_type,
                                confidence=0.7,
                            ))
                    break

        return relations

    @staticmethod
    def _find_nearest_entity(text_fragment: str, entities: List[str]) -> Optional[str]:
        if not entities:
            return None
        fragment = text_fragment.lower().strip()
        for entity in entities:
            if entity.lower() in fragment:
                return entity
        return None


class KnowledgeGraphStore:
    """知识图谱存储 - 基于 LangGraph Store 持久化"""

    def __init__(self, store=None):
        self._store = store
        self._local_graph: Dict[str, Dict[str, Any]] = {}

    def _ensure_store(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import ensure_store
        return ensure_store(self)

    @staticmethod
    def _namespace(user_id: int) -> tuple:
        return (str(user_id), "knowledge_graph")

    def save_graph(
        self,
        user_id: int,
        entities: List[Entity],
        relations: List[Relation],
    ) -> bool:
        graph_data = {
            "entities": {e.name: e.to_dict() for e in entities},
            "relations": {r.key: r.to_dict() for r in relations},
            "updated_at": time.time(),
        }

        cache_key = f"kg:{user_id}"
        self._local_graph[cache_key] = graph_data

        store = self._ensure_store()
        if store is None:
            return True

        namespace = self._namespace(user_id)
        try:
            store.put(namespace, "graph", graph_data)
            logger.debug(f"知识图谱已保存: user={user_id}, entities={len(entities)}, relations={len(relations)}")
            return True
        except Exception as e:
            logger.error(f"知识图谱保存到 Store 失败: {e}")
            return False

    def load_graph(self, user_id: int) -> Tuple[List[Entity], List[Relation]]:
        cache_key = f"kg:{user_id}"
        if cache_key in self._local_graph:
            data = self._local_graph[cache_key]
            return self._deserialize_graph(data)

        store = self._ensure_store()
        if store is not None:
            namespace = self._namespace(user_id)
            try:
                item = store.get(namespace, "graph")
                if item and hasattr(item, 'value'):
                    data = item.value
                    self._local_graph[cache_key] = data
                    return self._deserialize_graph(data)
            except Exception as e:
                logger.error(f"从 Store 加载知识图谱失败: {e}")

        return [], []

    def merge_graph(
        self,
        user_id: int,
        new_entities: List[Entity],
        new_relations: List[Relation],
    ) -> Tuple[List[Entity], List[Relation]]:
        existing_entities, existing_relations = self.load_graph(user_id)

        entity_map: Dict[str, Entity] = {e.name: e for e in existing_entities}
        for e in new_entities:
            if e.name in entity_map:
                entity_map[e.name].mention_count += e.mention_count
                entity_map[e.name].last_seen = e.last_seen
                entity_map[e.name].confidence = min(1.0, entity_map[e.name].confidence + 0.1)
                if e.properties:
                    entity_map[e.name].properties.update(e.properties)
            else:
                entity_map[e.name] = e

        relation_map: Dict[str, Relation] = {r.key: r for r in existing_relations}
        for r in new_relations:
            if r.key not in relation_map:
                relation_map[r.key] = r

        merged_entities = list(entity_map.values())
        merged_relations = list(relation_map.values())

        self.save_graph(user_id, merged_entities, merged_relations)
        return merged_entities, merged_relations

    @staticmethod
    def _deserialize_graph(data: Dict[str, Any]) -> Tuple[List[Entity], List[Relation]]:
        entities = [Entity.from_dict(e) for e in data.get("entities", {}).values()]
        relations = [Relation.from_dict(r) for r in data.get("relations", {}).values()]
        return entities, relations

    def get_related_context(
        self,
        user_id: int,
        query: str,
        max_hops: int = 2,
        max_entities: int = 20,
    ) -> str:
        entities, relations = self.load_graph(user_id)
        if not entities:
            return ""

        query_lower = query.lower()
        matched = set()
        for entity in entities:
            if entity.name.lower() in query_lower or query_lower in entity.name.lower():
                matched.add(entity.name)

        if not matched:
            sorted_entities = sorted(entities, key=lambda e: (e.mention_count, e.confidence), reverse=True)
            for e in sorted_entities[:3]:
                matched.add(e.name)

        expanded = self._expand_entities(matched, relations, max_hops)
        relevant_entities = [e for e in entities if e.name in expanded][:max_entities]
        relevant_relations = [
            r for r in relations
            if r.source in expanded or r.target in expanded
        ][:max_entities]

        return self._format_context(relevant_entities, relevant_relations)

    @staticmethod
    def _expand_entities(seed: Set[str], relations: List[Relation], max_hops: int) -> Set[str]:
        expanded = set(seed)
        frontier = set(seed)
        for _ in range(max_hops):
            next_frontier = set()
            for rel in relations:
                if rel.source in frontier and rel.target not in expanded:
                    next_frontier.add(rel.target)
                if rel.target in frontier and rel.source not in expanded:
                    next_frontier.add(rel.source)
            expanded.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return expanded

    @staticmethod
    def _format_context(entities: List[Entity], relations: List[Relation]) -> str:
        if not entities and not relations:
            return ""

        parts = ["【知识图谱上下文】"]

        if entities:
            parts.append("相关实体：")
            for e in entities:
                type_tag = f"[{e.entity_type}]" if e.entity_type != "concept" else ""
                parts.append(f"  - {e.name} {type_tag} (提及{e.mention_count}次)")

        if relations:
            parts.append("实体关系：")
            for r in relations:
                parts.append(f"  - {r.source} --[{r.relation_type}]--> {r.target}")

        return "\n".join(parts)


class ContextKnowledgeGraph:
    """知识图谱上下文管理器 - 统一接口"""

    def __init__(self, store=None):
        self._extractor = ConversationGraphExtractor()
        self._kg_store = KnowledgeGraphStore(store)

    def process_conversation(
        self,
        user_id: int,
        messages: List[Dict[str, Any]],
    ) -> Tuple[List[Entity], List[Relation]]:
        entities, relations = self._extractor.extract(messages)
        if entities or relations:
            merged_entities, merged_relations = self._kg_store.merge_graph(user_id, entities, relations)
            logger.info(
                f"知识图谱更新: user={user_id}, "
                f"提取 {len(entities)} 实体/{len(relations)} 关系, "
                f"合并后 {len(merged_entities)} 实体/{len(merged_relations)} 关系"
            )
            return merged_entities, merged_relations
        return self._kg_store.load_graph(user_id)

    def get_context_for_query(
        self,
        user_id: int,
        query: str,
        max_hops: int = 2,
        max_entities: int = 20,
    ) -> str:
        return self._kg_store.get_related_context(user_id, query, max_hops, max_entities)

    def get_full_graph(self, user_id: int) -> Tuple[List[Entity], List[Relation]]:
        return self._kg_store.load_graph(user_id)

    def clear_graph(self, user_id: int) -> bool:
        cache_key = f"kg:{user_id}"
        if cache_key in self._kg_store._local_graph:
            del self._kg_store._local_graph[cache_key]

        store = self._kg_store._ensure_store()
        if store is not None:
            namespace = KnowledgeGraphStore._namespace(user_id)
            try:
                store.delete(namespace, "graph")
                return True
            except Exception as e:
                logger.error(f"清除知识图谱失败: {e}")
        return True
