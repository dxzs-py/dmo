# 深度研究报告：Ollama 本地模型环境调查

## 摘要

本次研究通过**嵌套式子代理调度**结构（主 agent → 子代理 A → 子代理 B）完成了对当前环境中 Ollama 已安装本地模型的调查。子代理 B 实际执行 `ollama list` 命令，结果经子代理 A 中转返回主 agent。最终确认当前环境中共安装了 **7 个 Ollama 模型**，涵盖生成式大语言模型和嵌入向量模型两大类。

## 一、研究方法

### 1.1 调度架构
本研究采用三层嵌套代理调度结构，严格遵循"调度者与执行者分离"原则：

```
主 agent（调度者）
   └── 子代理 A（general-purpose，中间调度层）
         └── 子代理 B（general-purpose，实际执行者）
               └── 执行命令：ollama list
```

### 1.2 执行流程
1. 主 agent 制定研究计划并写入 `/plans/research_plan.md`
2. 主 agent 派发子代理 A，任务为在内部再派发子代理 B 执行 `ollama list`
3. 子代理 A 派发子代理 B 并调用 `wait_for_subagent` 等待其完成
4. 子代理 B 执行 `ollama list` 命令并返回完整输出
5. 子代理 A 将结果转交主 agent
6. 主 agent 整理笔记并撰写本报告

## 二、研究结果

### 2.1 Ollama 模型列表

执行 `ollama list` 命令后，当前环境中共检测到 **7 个模型**，详细如下：

| 模型名称 | 模型 ID | 大小 | 修改时间 |
|---------|---------|------|---------|
| ornith:latest | a75697c14589 | 5.6 GB | 4 weeks ago |
| lfm2.5:latest | 9cf756159fc2 | 5.2 GB | 2 months ago |
| dengcao/Qwen3-Embedding-4B:Q5_K_M | 7e8c9ad6885b | 2.9 GB | 2 months ago |
| bge-m3:latest | 790764642607 | 1.2 GB | 2 months ago |
| qwen3:8b | 500a1f067a9f | 5.2 GB | 2 months ago |
| qwen3.5:9b | 6488c96fa5fa | 6.6 GB | 3 months ago |
| hermes3:8b | 4f6b83f30b62 | 4.7 GB | 3 months ago |

### 2.2 模型分类分析

- **生成式大语言模型（LLM）**：ornith:latest、lfm2.5:latest、qwen3:8b、qwen3.5:9b、hermes3:8b（共 5 个）
- **嵌入向量模型（Embedding）**：dengcao/Qwen3-Embedding-4B:Q5_K_M、bge-m3:latest（共 2 个）

### 2.3 存储占用
全部 7 个模型累计占用约 **31.4 GB** 存储空间。

## 三、调度机制验证

本研究表明嵌套式子代理调度机制运行正常：
- 子代理 A 作为中间调度层能够正确派发下一级子代理 B
- 子代理 A 通过 `wait_for_subagent` 正确等待并获取子代理 B 的结果
- 结果在多层代理间完整、无损地逐级传递

## 四、结论与建议

### 结论
1. 当前环境已预装完整的 Ollama 运行环境和 7 个可用模型，具备多语言生成与向量检索能力。
2. 嵌套式子代理调度机制验证成功，可支持更复杂的多级任务分解。

### 建议
1. 若需进行 RAG（检索增强生成）应用开发，可直接使用已内置的 bge-m3 与 Qwen3-Embedding-4B 嵌入模型。
2. 生成任务可根据规模选择 qwen3.5:9b（较大，质量更高）或 qwen3:8b / hermes3:8b（较小，速度更快）。

## 五、参考文献

- 研究计划：`/plans/research_plan.md`
- 研究笔记：`/notes/web_research.md`
- 数据来源：子代理 B 执行 `ollama list` 命令的原始输出
