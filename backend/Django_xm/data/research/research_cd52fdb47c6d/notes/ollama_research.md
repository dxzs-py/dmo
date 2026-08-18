# 研究笔记：Ollama 模型环境检查

## 子代理 1 结果（ollama list + 询问模型"你是谁"）

### ollama list 输出
```
NAME                                 ID              SIZE      MODIFIED     
ornith:latest                        a75697c14589    5.6 GB    4 weeks ago     
lfm2.5:latest                        9cf756159fc2    5.2 GB    2 months ago    
dengcao/Qwen3-Embedding-4B:Q5_K_M    7e8c9ad6885b    2.9 GB    2 months ago    
bge-m3:latest                        790764642607    1.2 GB    2 months ago    
qwen3:8b                             500a1f067a9f    5.2 GB    2 months ago    
qwen3.5:9b                           6488c96fa5fa    6.6 GB    3 months ago    
hermes3:8b                           4f6b83f30b62    4.7 GB    3 months ago
```
共 7 个模型，涵盖编码助手、Embedding 模型及通用对话模型。

### 第一个模型
- 名称：`ornith:latest`（ID: a75697c14589，5.6 GB，4 周前更新）

### 模型对"你是谁"的回复
> 我是 **Ornith**，一个开源的 agentic 编码助手。
> 我可以帮助您：
> - 分析问题并逐步推理
> - 编写和调试代码
> - 审查和解释技术内容
> - 处理多步骤的复杂开发任务

## 子代理 2 结果（ollama ps）

### ollama ps 输出
```
NAME             ID              SIZE      PROCESSOR    CONTEXT    UNTIL              
ornith:latest    a75697c14589    5.3 GB    100% GPU     4096       4 minutes from now    
bge-m3:latest    790764642607    664 MB    100% GPU     4096       2 minutes from now
```
当前有 2 个模型正在运行，均 100% GPU 加速，上下文 4096 tokens。

## 关键观察
- `ornith:latest` 同时出现在 list 首位和 ps 运行列表中，是当前主用模型。
- 全部运行模型由 GPU 加速。
