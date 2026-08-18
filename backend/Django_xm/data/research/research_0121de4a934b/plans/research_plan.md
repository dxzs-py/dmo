# 深度研究计划：Ollama 本地模型环境调查

## 研究目标
通过嵌套式子代理调度，调查当前环境中 Ollama 已安装的本地模型列表。

## 调度结构
- 主 agent（调度者）：负责制定计划、派发子代理、等待结果、汇总报告
- 子代理 A（general-purpose）：作为中间调度层，负责派发子代理 B 并等待其结果
- 子代理 B（general-purpose）：实际执行 `ollama list` 命令

## 执行步骤
1. 主 agent 制定研究计划（本文档）
2. 主 agent 派发子代理 A（general-purpose），任务为：内部再派发子代理 B 执行 `ollama list`，并用 wait_for_subagent 等待 B 完成后返回结果
3. 主 agent 等待子代理 A 完成并获取结果
4. 主 agent 整理研究笔记
5. 主 agent 撰写最终报告

## 期望产出
- Ollama 本地模型列表（模型名称、标签、大小等信息）
- 嵌套式代理调度的执行效果说明
