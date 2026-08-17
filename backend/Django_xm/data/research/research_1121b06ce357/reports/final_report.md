# 研究报告：Ollama 模型状态汇总

## 摘要
本报告汇总了并行执行 `ollama list` 与 `ollama ps` 两个命令的结果。当前环境中已安装 **7 个 Ollama 模型**，且 Ollama 服务运行正常；但当前 **没有任何模型处于运行状态**（所有模型均未被加载到内存）。

## 1. 已安装模型列表（ollama list）

| 模型名称 | ID | 大小 | 修改时间 |
|---|---|---|---|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

### 模型构成分析
- **聊天/生成模型**（5 个）：qwen3:8b、qwen3.5:9b、hermes3:8b、ornith:latest、lfm2.5:latest
- **嵌入模型**（2 个）：bge-m3、dengcao/Qwen3-Embedding-4B（适合 RAG 检索增强生成场景）
- **体积最大**：qwen3.5:9b（6.6 GB）
- **体积最小**：bge-m3（1.2 GB）
- **最近更新**：ornith:latest（4 周前）

## 2. 运行中的模型状态（ollama ps）

命令执行成功（退出码 0），但输出**仅包含表头，没有任何模型记录**：

```
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL
```

这表明：
- ✅ Ollama 服务**正常运行**
- ⚠️ 当前**没有模型被加载到内存中运行**（无活动负载）

## 3. 结论与建议

### 结论
- 环境中已安装丰富多样的 Ollama 模型，覆盖生成与嵌入两类用途。
- 当前系统处于空闲状态，无模型占用内存资源。

### 建议
1. **按需加载**：运行模型时可用 `ollama run <模型名>` 触发加载，`ollama ps` 会显示其内存占用、处理器（CPU/GPU）分配及上下文窗口。
2. **内存管理**：由于当前无模型常驻内存，可放心运行大模型（如 qwen3.5:9b）而无需担心内存冲突。
3. **深入排查**：如需了解某模型的参数量、量化精度等细节，可运行 `ollama show <模型名>`。

## 参考文献
- 子代理 1 执行 `ollama list` 的标准输出（7 个已安装模型记录）
- 子代理 2 执行 `ollama ps` 的标准输出（仅表头，无运行中模型）
- 研究笔记：/notes/ollama_research.md
