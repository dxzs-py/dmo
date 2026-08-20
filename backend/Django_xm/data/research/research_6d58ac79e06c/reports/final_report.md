# Ollama 状态检查报告（嵌套子代理调度）

## 摘要
本报告基于嵌套子代理调度架构，对本地 Ollama 实例进行了状态检查。主 agent 并行派发两个 general-purpose 子代理 A1、A2，二者各自再派发底层子代理 B1、B2 执行命令，最终收集到 **7 个已下载模型** 与 **1 个当前运行中的模型**。调度链路完整、命令执行全部成功。

## 调度架构说明
```
主 agent
 ├─ spawn A1 (general-purpose)
 │    └─ spawn B1 → 执行 `ollama list` → wait_for_subagent(B1)
 └─ spawn A2 (general-purpose)
      └─ spawn B2 → 执行 `ollama ps` → wait_for_subagent(B2)
主 agent → wait_for_subagent(A1, A2) → 汇总
```
所有实际命令执行均由最底层子代理（B1、B2）完成，中间层（A1、A2）仅负责委派与结果转交，主 agent 仅负责调度与汇总。

## 1. 已下载模型清单（`ollama list`）
| 模型名称 | ID | 大小 | 最近修改 |
|---------|----|------|---------|
| ornith:latest | a75697c14589 | 5.6 GB | 5 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 2 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

**共 7 个模型**，命令执行成功，无错误。

## 2. 运行中的模型（`ollama ps`）
| 模型名称 | ID | 大小 | 处理器 | 上下文 | 卸载时间 |
|---------|----|------|--------|--------|---------|
| qwen3:8b | 500a1f067a9f | 5.6 GB | 100% GPU | 4096 | 2 minutes from now |

当前仅 `qwen3:8b` 处于运行状态，占用 5.6 GB 显存，100% GPU 加速，上下文长度 4096，预计约 2 分钟后自动卸载（UNTIL 字段）。命令退出码 0，无 stderr 输出。

## 3. 交叉分析
- `qwen3:8b` 同时出现在 `ollama list` 和 `ollama ps` 中，说明该模型处于**已下载且当前加载运行**的状态。
- 其余 6 个模型（ornith、lfm2.5、Qwen3-Embedding-4B、bge-m3、qwen3.5:9b、hermes3:8b）均为**已下载但当前未运行**状态。
- 环境中包含通用 LLM（qwen3、qwen3.5、hermes3）、嵌入模型（bge-m3、Qwen3-Embedding-4B）及若干专用模型（ornith、lfm2.5），用途覆盖对话与 RAG 检索场景。

## 4. 结论与建议
- **结论**：嵌套子代理调度成功执行，两层委派（A→B）链路正常，7 个模型已就绪，1 个模型正在运行。Ollama 服务运行正常。
- **建议**：
  1. `qwen3:8b` 约 2 分钟后自动卸载，若需持续服务可考虑调整 `OLLAMA_KEEP_ALIVE` 环境变量。
  2. 若显存（GPU）资源紧张，可优先加载 1.2 GB 的 `bge-m3` 或 2.9 GB 的嵌入模型用于轻量任务。
  3. 定期清理长期未使用的模型以释放磁盘空间（如 6.6 GB 的 qwen3.5:9b）。

## 参考文献 / 数据来源
- 子代理 A1 → B1 执行的 `ollama list` 输出（本机实时数据）
- 子代理 A2 → B2 执行的 `ollama ps` 输出（本机实时数据）
- Ollama 官方命令行文档（`ollama list` / `ollama ps` 语义）

---

*报告生成时间：本会话；数据来源：本地 Ollama 实例实时输出。*
