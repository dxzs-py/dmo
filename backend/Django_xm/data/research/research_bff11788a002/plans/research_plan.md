# Redis 核心数据结构深度研究计划

## 研究目标
深入调研 Redis 的核心数据结构，覆盖：
1. 五大基本数据类型（String、List、Hash、Set、ZSet）的对外语义与典型应用
2. 底层实现（SDS、双向链表、压缩列表/listpack、跳表、整数集合、哈希表等）
3. 编码转换规则（encoding 优化策略）
4. 高级数据类型（Bitmap、HyperLogLog、Geo、Stream）
5. 各结构在实战中的选型建议

## 研究步骤
1. **知识库文档分析**：调用 doc-analyst 子智能体检索知识库 Redis 文档集，重点提取"02 数据类型与底层实现"等文档内容
2. **网络补充研究**：调用 general-purpose 子智能体搜索官方文档与权威资料，核实最新版本（Redis 7.x）的底层实现变化
3. **笔记整理**：将知识库分析结果写入 `/notes/doc_analysis.md`，网络研究结果写入 `/notes/web_research.md`
4. **报告撰写**：整合所有素材，撰写结构化研究报告保存至 `/reports/final_report.md`

## 关键调研问题
- Redis 五大基本数据类型分别适用于什么场景？
- 各数据类型的底层实现是什么（SDS/listpack/跳表/哈希表/整数集合）？
- Redis 7.0 引入 listpack 后，ziplist 是否完全被替代？
- 编码转换（encoding 切换）的触发条件是什么？
- Bitmap/HyperLogLog/Geo/Stream 的原理与内存优势？
- 实际项目中如何选型？
