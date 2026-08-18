# 深度研究报告：Ollama 运行状态与本地模型清单调查

## 摘要

本报告基于多级子代理调度架构，对当前环境中 Ollama 的本地模型清单（`ollama list`）与运行中模型状态（`ollama ps`）进行了深度调查。

调查结果显示：当前环境共拉取了 **7 个本地模型**（5 个对话/生成模型 + 2 个嵌入模型），当前仅有 **1 个模型**（`bge-m3:latest`）正在运行，完全由 GPU 承载。

本任务完整实践了"主 agent 调度 → 并行派发 A1/A2 → 各自内部再派发 B1/B2 执行命令 → 逐级等待汇总"的嵌套式子代理调度流程。

---

## 一、调度架构与执行链路

本任务采用三级子代理调度架构，严格遵循"主 agent 只调度、不执行"的原则：

```
主 agent（调度者）
├── 并行派发 A1 (general-purpose) ──→ 内部派发 B1 ──→ shell_exec: ollama list
└── 并行派发 A2 (general-purpose) ──→ 内部派发 B2 ──→ shell_exec: ollama ps
```

**执行链路明细：**

| 层级 | 代理 | 职责 | 内部子代理 |
|------|------|------|-----------|
| 一级 | 主 agent | 制定计划、派发、汇总 | A1、A2 |
| 二级 | A1 | 派发并等待 B1 | B1 (`ollama list`) |
| 二级 | A2 | 派发并等待 B2 | B2 (`ollama ps`) |
| 三级 | B1/B2 | 执行本地命令 | — |

所有业务工具（`shell_exec`）均由最底层的 B1、B2 执行，A1/A2 及主 agent 均通过 `spawn_sub_agent` + `wait_for_subagent` 完成逐级委派与等待。

---

## 二、研究结果：`ollama list`（本地模型清单）

通过 B1 执行 `ollama list`，共获取 **7 个已拉取的本地模型**：

| NAME | ID | SIZE | MODIFIED |
|------|-----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

**按用途分类：**
- **对话/生成模型（5 个）**：ornith:latest、lfm2.5:latest、qwen3:8b、qwen3.5:9b、hermes3:8b
- **嵌入模型（2 个）**：dengcao/Qwen3-Embedding-4B:Q5_K_M、bge-m3:latest

模型总磁盘占用约 **31.4 GB**。其中 qwen3.5:9b 体积最大（6.6 GB），bge-m3 最小（1.2 GB）。

---

## 三、研究结果：`ollama ps`（运行中模型状态）

通过 B2 执行 `ollama ps`，当前 **仅有 1 个模型正在运行**：

| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|-----|------|-----------|---------|-------|
| bge-m3:latest | 790764642607 | 664 MB | 100% GPU | 4096 | 2 minutes from now |

**关键信息解读：**
- **运行模型**：`bge-m3:latest`（多语言嵌入模型，常用于文本向量化 / RAG 检索场景）
- **硬件承载**：100% 由 GPU 加载运行
- **显存占用**：约 664 MB（远小于其磁盘镜像 1.2 GB，符合量化加载特性）
- **上下文窗口**：4096 tokens
- **驻留状态**：剩余约 2 分钟自动卸载（Ollama 默认闲置卸载机制）

---

## 四、交叉分析

1. **运行模型与模型清单的关联**：`bge-m3:latest` 同时出现在 `ollama list`（ID: 790764642607）和 `ollama ps`（ID: 790764642607）中，ID 完全一致，验证了数据一致性。
2. **运行模型稀缺性**：7 个模型中仅 1 个（bge-m3）当前处于运行态，其余均为闲置状态。
3. **运行态为嵌入模型**：当前活跃的是嵌入模型而非对话模型，说明近期任务场景偏向文本向量化 / 检索增强（RAG），而非生成式对话。

---

## 五、结论与建议

### 结论
- 当前环境 Ollama 已就绪，拥有 7 个本地模型，覆盖对话生成与嵌入两大用途。
- 当前仅 1 个模型（bge-m3）处于运行状态，由 GPU 承载，显存占用约 664 MB。
- 嵌套式多级子代理调度流程执行成功，主 agent 全程仅负责调度，业务命令全部由底层子代理执行。

### 建议
1. **资源优化**：若近期无需生成式对话，可考虑清理 ornith、lfm2.5 等不常用的大体积模型，释放约 10+ GB 磁盘空间。
2. **运行态监测**：bge-m3 约 2 分钟后自动卸载，若需持续使用可提前预热或调整 Ollama `keep_alive` 参数（`OLLAMA_KEEP_ALIVE`）。
3. **调度架构复用**：本任务的嵌套式三级子代理调度模型可复用于其他"需隔离执行权限"或"分层并行"的调查场景。

---

## 六、参考文献

- `ollama list` 命令输出（B1 子代理执行结果）
- `ollama ps` 命令输出（B2 子代理执行结果）
- 研究计划：`/plans/research_plan.md`
- 研究笔记：`/notes/web_research.md`
