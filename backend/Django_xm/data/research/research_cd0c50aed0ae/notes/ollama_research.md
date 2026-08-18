# Ollama 运行状态研究笔记

## 来源
- 子代理 1（general-purpose，thread: subagent_ae811ae443b44ed3）：ollama list + 询问第一个模型"你是谁"
- 子代理 2（general-purpose，thread: subagent_49308840b82e4b7a）：ollama ps

## 子代理 1 结果：ollama list（已安装模型列表）

本机共安装 **7 个模型**：

| NAME | ID | SIZE | MODIFIED |
|------|-----|------|----------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

涵盖通用对话模型（ornith、qwen3、qwen3.5、hermes3、lfm2.5）和嵌入模型（bge-m3、Qwen3-Embedding-4B）。

### 第一个模型 ornith:latest 的自我介绍
> 我是 Ornith，一个开源的 agentic coding assistant。我可以帮你编写代码、调试问题、解释技术概念，或者协助完成各种编程任务。有什么需要帮忙的？

## 子代理 2 结果：ollama ps（当前运行模型）

当前 **1 个模型正在运行**：

| NAME | ID | SIZE | PROCESSOR | CONTEXT | UNTIL |
|------|-----|------|-----------|---------|-------|
| ornith:latest | a75697c14589 | 5.3 GB | 100% GPU | 4096 | 4 minutes from now |

- 模型 100% 在 GPU 上运行，未使用 CPU 推理
- 上下文窗口长度 4096 tokens
- 约 4 分钟后因空闲自动卸载（默认空闲 5 分钟卸载，说明约 1 分钟前被调用）
