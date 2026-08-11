# Redis 核心数据结构深度研究计划

## 研究目标
深入、系统地研究 Redis 的核心数据结构，包括：
1. Redis 对外提供的五种（或更多）核心数据类型（String、List、Hash、Set、Sorted Set）的定义、特点与使用场景
2. 每种数据类型的底层编码实现（如 SDS、ziplist、quicklist、skiplist、intset、hashtable 等）
3. Redis 7.x/8.x 引入的新数据结构（如 Stream、Bitmap、HyperLogLog、GEO 等扩展类型）及其底层实现
4. 底层编码的触发条件与转换规则（如 listpack 与 hash 的小数据编码优化）
5. 各数据结构的典型应用场景与实践建议

## 研究方法
- **步骤 1（本文件）**：制定研究计划 ✅
- **步骤 2**：委派 doc-analyst 子智能体检索知识库中的 Redis 相关文档，获取已有文档内容
- **步骤 3**：委派 web-researcher 子智能体从互联网补充官方文档资料与最新版本特性
- **步骤 4**：整理文档分析笔记 → /notes/doc_analysis.md
- **步骤 5**：整理网络研究笔记 → /notes/web_research.md
- **步骤 6**：综合所有资料撰写最终报告 → /reports/final_report.md

## 研究产出目录
- /plans/research_plan.md — 本计划文件
- /notes/doc_analysis.md — 知识库文档分析笔记
- /notes/web_research.md — 网络研究笔记
- /reports/final_report.md — 最终研究报告

## 时间安排
| 步骤 | 内容 | 预计产出 |
|------|------|----------|
| 1 | 制定计划 | research_plan.md |
| 2 | 知识库文档分析 | doc_analysis.md |
| 3 | 网络资料补充 | web_research.md |
| 4 | 综合撰写报告 | final_report.md |
