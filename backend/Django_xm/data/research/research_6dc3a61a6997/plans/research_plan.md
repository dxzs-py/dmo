# 研究计划：Redis 核心数据结构深度研究

## 研究目标
系统梳理 Redis 的核心数据结构，包括：
1. 五大基础数据类型（String、List、Hash、Set、ZSet/Sorted Set）
2. 扩展数据类型（Bitmap、HyperLogLog、Geo、Stream、Bitfield）
3. 每种数据结构的底层实现（SDS、linkedlist/quicklist、dict、intset、skiplist、ziplist/listpack）
4. 编码转换机制与内存优化
5. 典型应用场景与最佳实践

## 研究步骤
- [x] 步骤 0：初步检索知识库，定位相关文档（02 数据类型与底层实现）
- [x] 步骤 1：编写研究计划（本文件）
- [x] 步骤 2：调度 doc-analyst 深度分析知识库中 Redis 数据类型相关文档
- [x] 步骤 3：调度 web-researcher 补充外部权威资料（Redis 官方文档、底层实现原理）
- [x] 步骤 4：整理研究笔记到 /notes/web_research.md 与 /notes/doc_analysis.md
- [x] 步骤 5：整合撰写最终报告到 /reports/final_report.md

## 资料来源
1. 知识库：data/uploads/user_1_redis/02 数据类型与底层实现（核心）
2. 知识库：data/uploads/user_1_redis/00 总览与导读（术语表）
3. 外部：Redis 官方文档 redis.io、源码（object.c / t_string.c / t_list.c 等）

## 输出交付物
- /plans/research_plan.md — 研究计划
- /notes/doc_analysis.md — 知识库文档分析笔记
- /notes/web_research.md — 网络研究笔记
- /reports/final_report.md — 最终研究报告
