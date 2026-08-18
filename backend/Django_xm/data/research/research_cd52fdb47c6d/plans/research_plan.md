# 研究计划：Ollama 模型环境检查

## 目标
1. 运行 `ollama list` 查看本地已安装的 Ollama 模型列表
2. 对列表中的第一个模型发起询问"你是谁"，获取模型的自我认知回复
3. 运行 `ollama ps` 查看当前正在运行的模型进程状态
4. 汇总两个子代理的结果

## 子任务划分
- **子代理 1**（general-purpose）：执行 `ollama list`，解析输出中的第一个模型名，然后调用该模型询问"你是谁"
- **子代理 2**（general-purpose）：执行 `ollama ps`

## 执行策略
- 子代理 1 和子代理 2 相互独立，并行派发
- 使用 wait_for_subagent 等待两个子代理全部完成

## 产出
- 研究笔记：/notes/ollama_research.md
- 最终报告：向用户汇总结果
