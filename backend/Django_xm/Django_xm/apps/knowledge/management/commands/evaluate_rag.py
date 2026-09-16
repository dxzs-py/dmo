"""RAG 检索质量评估命令

指标：Recall@K / Precision@K / HitRate / MRR / 检索时延(P50/P95)
策略消融：similarity(纯向量) / similarity + FlashRank 重排序 / PostgreSQL 全文检索降级

用法：
    conda activate langchain_xm
    cd backend/Django_xm

    # 1) 构建/重建评测语料索引（默认 22 篇 Redis 文档 → pgvector）
    python manage.py evaluate_rag --build-corpus --corpus data/eval/corpus/redis --kb eval_redis

    # 2) 三种策略全量评估，输出 Markdown 报告
    python manage.py evaluate_rag --kb eval_redis --eval-set data/eval/rag_eval_set.jsonl \
        --output data/eval/rag_report.md

    # 3) 只评估单一策略
    python manage.py evaluate_rag --kb eval_redis --eval-set data/eval/rag_eval_set.jsonl \
        --strategies dense --top-k 6

评测集格式（JSONL，逐行一个 case）：
    {"query": "...", "relevant": ["03_持久化机制.md"]}
    relevant 为「该问题答案所在的源文件名」，按文件名（basename）匹配。
"""

import json
import os
import statistics
import time

from django.core.management.base import BaseCommand, CommandError
from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.services.document_service import (
    load_documents_from_directory,
)
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.rag_evaluation import RAGEvaluator
from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
from Django_xm.apps.knowledge.services.splitters import split_documents

logger = get_logger(__name__)

ALL_STRATEGIES = ("dense", "dense+rerank", "keyword")


def _basename(value) -> str:
    """统一 doc id 口径：取 source/file_name 的 basename，与评测集标注可比。"""
    if not value:
        return ""
    return os.path.basename(str(value).replace("\\", "/"))


def _normalize_provenance(docs: list[Document]) -> list[Document]:
    """把检索结果的 source 归一化为文件名。

    RAGEvaluator.evaluate_retrieval 以 doc.metadata['source'] 作为 doc id，
    而索引中的 source 是绝对路径；此处统一为 basename 后再比对，避免出现
    「同一文档但 id 不相等」导致的假阴性。不修改原始 Document。
    """
    normalized: list[Document] = []
    for doc in docs:
        name = _basename(doc.metadata.get("file_name") or doc.metadata.get("source"))
        meta = dict(doc.metadata)
        meta["source"] = name
        normalized.append(Document(page_content=doc.page_content, metadata=meta))
    return normalized


def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * p), len(ordered) - 1)
    return round(ordered[idx], 1)


class Command(BaseCommand):
    help = "RAG 检索质量评估：Recall / Precision / HitRate / MRR / 时延，支持多策略消融"

    def add_arguments(self, parser):
        parser.add_argument("--kb", required=True, help="索引（知识库）名称，如 eval_redis")
        parser.add_argument("--eval-set", default=None, help="评测集 JSONL 路径")
        parser.add_argument("--build-corpus", action="store_true", help="先构建/重建语料索引")
        parser.add_argument("--corpus", default=None, help="语料目录（配合 --build-corpus）")
        parser.add_argument("--top-k", type=int, default=4, help="Top-K，默认 4")
        parser.add_argument(
            "--strategies",
            default=",".join(ALL_STRATEGIES),
            help=f"要评估的策略，逗号分隔，可选 {ALL_STRATEGIES}",
        )
        parser.add_argument("--output", default=None, help="Markdown 报告输出路径")

    # ── 语料索引构建 ──────────────────────────────────────────────────────────

    def _build_corpus(self, corpus_dir: str, kb: str, embeddings) -> dict:
        if not corpus_dir or not os.path.isdir(corpus_dir):
            raise CommandError(f"语料目录不存在: {corpus_dir}")

        self.stdout.write(f"加载语料目录: {corpus_dir}")
        raw_docs = load_documents_from_directory(corpus_dir, recursive=True, extensions=[".md", ".txt"])
        if not raw_docs:
            raise CommandError("语料目录中未加载到任何文档")

        chunks = split_documents(raw_docs)
        total_chars = sum(len(c.page_content) for c in chunks)
        stats = {
            "file_count": len({_basename(d.metadata.get("file_name") or d.metadata.get("source")) for d in raw_docs}),
            "raw_doc_count": len(raw_docs),
            "chunk_count": len(chunks),
            "avg_chunk_chars": round(total_chars / len(chunks), 1) if chunks else 0,
        }
        self.stdout.write(
            f"语料统计: {stats['file_count']} 个文件 / {stats['raw_doc_count']} 个原始文档块 "
            f"→ 切分为 {stats['chunk_count']} 个文本块（平均 {stats['avg_chunk_chars']} 字符）"
        )

        t0 = time.perf_counter()
        manager = IndexManager()
        manager.create_index(
            name=kb,
            documents=chunks,
            embeddings=embeddings,
            description="RAG retrieval evaluation corpus",
            store_type="pgvector",
            overwrite=True,
        )
        stats["build_seconds"] = round(time.perf_counter() - t0, 1)
        self.stdout.write(self.style.SUCCESS(f"索引 {kb} 构建完成，耗时 {stats['build_seconds']}s"))
        return stats

    # ── 评测集加载 ────────────────────────────────────────────────────────────

    @staticmethod
    def _load_eval_set(path: str) -> list[dict]:
        if not path or not os.path.exists(path):
            raise CommandError(f"评测集不存在: {path}")
        cases: list[dict] = []
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CommandError(f"评测集第 {lineno} 行不是合法 JSON: {exc}") from exc
                if "query" not in item or "relevant" not in item:
                    raise CommandError(f"评测集第 {lineno} 行缺少 query/relevant 字段")
                cases.append(item)
        if not cases:
            raise CommandError("评测集为空")
        return cases

    # ── 检索策略 ──────────────────────────────────────────────────────────────

    def _make_retriever(self, strategy: str, store, top_k: int):
        if strategy == "dense":
            return create_retriever(store, search_type="similarity", k=top_k)
        if strategy == "dense+rerank":
            return create_retriever(store, search_type="similarity", k=top_k, use_reranker=True)
        if strategy == "keyword":
            from Django_xm.apps.knowledge.services.retrieval_service import (
                _keyword_search_fallback,
            )

            return _keyword_search_fallback
        raise CommandError(f"未知策略: {strategy}")

    def _run_strategy(self, strategy: str, retriever, cases: list[dict], top_k: int) -> dict:
        evaluator = RAGEvaluator(llm=None)
        rows: list[dict] = []
        latencies: list[float] = []
        precisions: list[float] = []
        doc_precisions: list[float] = []
        recalls: list[float] = []
        hit_rates: list[float] = []
        mrrs: list[float] = []

        for idx, case in enumerate(cases, 1):
            query = case["query"]
            relevant = {_basename(item) for item in case["relevant"]}

            t0 = time.perf_counter()
            try:
                if strategy == "keyword":
                    docs = retriever(query, self.kb_name, top_k)
                else:
                    docs = retriever.invoke(query)
            except Exception as exc:
                logger.warning(f"[{strategy}][{idx}] 检索失败: {exc}")
                docs = []
            latency_ms = (time.perf_counter() - t0) * 1000
            latencies.append(latency_ms)

            docs = _normalize_provenance(docs or [])
            metrics = evaluator.evaluate_retrieval(query, docs, list(relevant))

            # 文档级精度：ground truth 为文件级，故分母取「返回结果涉及的文档数」，
            # 与分子（命中文档数）同量纲。直接使用 chunk 级 precision 会因
            # 同一文档多个 chunk 同时进入 Top-K 而系统性低估（实测 0.25 vs 1.00）。
            retrieved_doc_ids = list(dict.fromkeys(_basename(d.metadata.get("source")) for d in docs))
            hit_doc_ids = [doc_id for doc_id in retrieved_doc_ids if doc_id in relevant]
            doc_precisions.append(len(hit_doc_ids) / len(retrieved_doc_ids) if retrieved_doc_ids else 0.0)

            precisions.append(metrics.precision)
            recalls.append(metrics.recall)
            hit_rates.append(metrics.hit_rate)
            mrrs.append(metrics.mrr)

            top = _basename(docs[0].metadata.get("source")) if docs else "-"
            rows.append(
                {
                    "query": query,
                    "expected": sorted(relevant),
                    "retrieved": [_basename(d.metadata.get("source")) for d in docs],
                    "top1": top,
                    "recall": metrics.recall,
                    "precision": metrics.precision,
                    "hit_rate": metrics.hit_rate,
                    "mrr": metrics.mrr,
                    "latency_ms": round(latency_ms, 1),
                }
            )

        return {
            "strategy": strategy,
            "k": top_k,
            "case_count": len(cases),
            "recall": _mean(recalls),
            "precision": _mean(precisions),
            "precision_doc": _mean(doc_precisions),
            "hit_rate": _mean(hit_rates),
            "mrr": _mean(mrrs),
            "latency_p50_ms": _percentile(latencies, 0.50),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "rows": rows,
        }

    # ── 主流程 ────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        kb = options["kb"]
        self.kb_name = kb
        top_k = options["top_k"]
        embeddings = get_embeddings(use_cache=True)

        # 预热 embedding：否则首个被评估的策略会独自承担 provider 初始化与首次请求的冷启动
        # 开销，造成策略之间时延指标不可比（实测差异可达 4 倍）。
        try:
            embeddings.embed_query("warmup")
        except Exception as exc:  # 预热失败不影响评测结果
            logger.warning(f"embedding 预热失败（不影响评测）: {exc}")

        corpus_stats = None
        if options["build_corpus"]:
            corpus_stats = self._build_corpus(options["corpus"], kb, embeddings)

        eval_path = options["eval_set"]
        if not eval_path:
            if corpus_stats:
                self.stdout.write(self.style.WARNING("仅构建语料，未指定 --eval-set，跳过评估"))
                return
            raise CommandError("请通过 --eval-set 指定评测集，或使用 --build-corpus 仅构建语料")

        strategies = [s.strip() for s in options["strategies"].split(",") if s.strip()]
        for name in strategies:
            if name not in ALL_STRATEGIES:
                raise CommandError(f"未知策略: {name}，可选 {ALL_STRATEGIES}")

        cases = self._load_eval_set(eval_path)
        self.stdout.write(f"评测集: {eval_path}（{len(cases)} 题）")

        manager = IndexManager()
        try:
            store = manager.load_index(kb, embeddings)
        except Exception as exc:
            raise CommandError(f"加载索引 {kb} 失败: {exc}") from exc

        results = []
        for strategy in strategies:
            self.stdout.write(f"\n=== 策略: {strategy} (K={top_k}) ===")
            retriever = self._make_retriever(strategy, store, top_k)
            result = self._run_strategy(strategy, retriever, cases, top_k)
            results.append(result)
            self.stdout.write(
                f"Recall@{top_k}={result['recall']:.4f}  "
                f"Precision@{top_k}={result['precision']:.4f}  "
                f"HitRate={result['hit_rate']:.4f}  "
                f"MRR={result['mrr']:.4f}  "
                f"P50={result['latency_p50_ms']}ms  P95={result['latency_p95_ms']}ms"
            )

        report = self._render_report(kb, top_k, corpus_stats, results)
        self.stdout.write("\n" + report)

        output = options["output"]
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(report)
            with open(os.path.splitext(output)[0] + ".json", "w", encoding="utf-8") as fh:
                json.dump(
                    {"kb": kb, "top_k": top_k, "corpus": corpus_stats, "results": results},
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
            self.stdout.write(self.style.SUCCESS(f"\n报告已写入: {output}"))

    # ── 报告渲染 ──────────────────────────────────────────────────────────────

    def _render_report(self, kb, top_k, corpus_stats, results) -> str:
        lines = [
            "# RAG 检索质量评估报告",
            "",
            f"- 索引：`{kb}`",
            f"- Top-K：{top_k}",
            f"- 评测集：{results[0]['case_count'] if results else 0} 题（标注口径：章节级 ground truth，按源文件名匹配）",
        ]
        if corpus_stats:
            lines += [
                f"- 语料：{corpus_stats['file_count']} 个文件 / {corpus_stats['raw_doc_count']} 个原始文档块 "
                f"→ {corpus_stats['chunk_count']} 个文本块（平均 {corpus_stats['avg_chunk_chars']} 字符）",
                f"- 索引构建耗时：{corpus_stats['build_seconds']}s",
            ]

        lines += [
            "",
            "## 策略对比",
            "",
            f"| 检索策略 | Recall@{top_k} | Precision@{top_k}（文档级） | HitRate | MRR | 时延 P50 | 时延 P95 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for res in results:
            lines.append(
                f"| {res['strategy']} | **{res['recall']:.3f}** | {res['precision_doc']:.3f} | "
                f"{res['hit_rate']:.3f} | {res['mrr']:.3f} | {res['latency_p50_ms']} ms | {res['latency_p95_ms']} ms |"
            )

        for res in results:
            lines += [
                "",
                f"## 逐题明细 · {res['strategy']}",
                "",
                "| # | Query | 期望 | Top-1 命中 | Recall | MRR | 时延(ms) |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for i, row in enumerate(res["rows"], 1):
                expected = row["expected"][0] if row["expected"] else "-"
                top1_hit = "✅" if any(r in row["expected"] for r in row["retrieved"]) else "❌"
                query = row["query"] if len(row["query"]) <= 32 else row["query"][:32] + "…"
                lines.append(
                    f"| {i} | {query} | {expected} | {top1_hit} | {row['recall']:.2f} | "
                    f"{row['mrr']:.2f} | {row['latency_ms']} |"
                )

        lines += [
            "",
            "> **指标口径说明**",
            ">",
            "> - ground truth 为**文件级**（relevant 标注到源文件），检索单元为 **chunk**，两者粒度不同。",
            "> - `Recall@K` = 命中的文档数 / 期望文档数；`HitRate` = 是否至少命中 1 篇期望文档；",
            ">   `MRR` = 首个命中文档排名的倒数。三者均为文档级口径。",
            "> - `Precision@K（文档级）` = 命中文档数 / 返回结果涉及的文档数。若改用 chunk 当分母，",
            ">   会因同一文档的多个 chunk 同时进入 Top-K 而被系统性低估（本项目实测 0.25 vs 1.00）。",
            "> - 时延为单进程内预热后测得；不同策略对比需各自独立进程运行，否则首个策略会承担冷启动开销。",
            "> - 本评测集仅 22 篇语料、问题按章节范围构造，存在**天花板效应**（Recall 易饱和），",
            ">   更适合作为回归基线（regression baseline），而非模型选型依据。",
        ]
        return "\n".join(lines)
