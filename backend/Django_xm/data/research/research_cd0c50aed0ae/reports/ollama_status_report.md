# Ollama 运行状态汇总报告

## 摘要

本报告通过并行派发两个子代理，分别执行 `ollama list`（含询问第一个模型的自我介绍）和 `ollama ps`，汇总了本机 Ollama 环境的模型安装情况与当前运行状态。结果显示本机共安装 **7 个模型**，当前正在运行的是 **ornith:latest**（100% GPU 推理），该模型自我识别为「Ornith，一个开源的 agentic coding assistant」。

## 一、已安装模型列表（ollama list）

本机共安装 7 个模型，涵盖通用对话模型与嵌入模型两类：

| 序号 | 模型名称 | ID | 大小 | 修改时间 |
|------|---------|-----|------|----------|
| 1 | ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| 2 | lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| 3 | dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| 4 | bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| 5 | qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| 6 | qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| 7 | hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

### 模型分类
- **通用对话模型**：ornith、lfm2.5、qwen3:8b、qwen3.5:9b、hermes3:8b（合计 5 个）
- **嵌入模型**：dengcao/Qwen3-Embedding-4B、bge-m3（合计 2 个）

## 二、第一个模型的自我介绍

根据 `ollama list` 结果，第一个模型为 **ornith:latest**，询问"你是谁"后其自我介绍如下：

> **我是 Ornith，一个开源的 agentic coding assistant。我可以帮你编写代码、调试问题、解释技术概念，或者协助完成各种编程任务。有什么需要帮忙的？**

## 三、当前运行状态（ollama ps）

当前本机 **1 个模型正在运行**，即 **ornith:latest**：

| 字段 | 值 | 含义 |
|------|-----|------|
| NAME | ornith:latest | 正在运行的模型 |
| ID | a75697c14589 | 模型唯一标识符 |
| SIZE | 5.3 GB | 已加载到内存/显存的大小 |
| PROCESSOR | 100% GPU | 全部在 GPU 上推理，未用 CPU |
| CONTEXT | 4096 | 上下文窗口长度 4096 tokens |
| UNTIL | 4 minutes from now | 空闲约 4 分钟后自动卸载 |

> 说明：Ollama 默认在模型空闲 5 分钟后自动从内存卸载以释放资源。当前显示还有约 4 分钟，说明该模型是在约 1 分钟前被调用的。

## 四、结论与建议

1. **环境状态健康**：Ollama 运行正常，模型管理正常，GPU 推理工作正常。
2. **当前活跃模型**：ornith:latest 是最近被调用的模型，也是对话功能的主力，自我定位为 coding assistant。
3. **建议**：
   - 若需释放显存/内存，可等待 ornith 空闲自动卸载或手动执行 `ollama stop ornith:latest`。
   - 如需查看模型更详细参数（量化级别等），可执行 `ollama show <模型名>`。
   - 本机已有 qwen3、qwen3.5、hermes3 等通用模型，可按需对比或管理（删除、拉取新模型）。

## 参考文献

- 子代理 1 执行结果（`ollama list` + `ollama run ornith:latest "你是谁"`）
- 子代理 2 执行结果（`ollama ps`）
- 研究笔记：/notes/ollama_research.md
