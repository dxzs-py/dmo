# 研究计划：并行执行 ollama list 与 ollama ps

## 目标
并行派发 2 个子代理，分别运行 `ollama list` 和 `ollama ps`，汇总两个命令的输出结果。

## 研究步骤
1. 制定研究计划（本文件）
2. 并行派发两个子代理：
   - 子代理 1（general-purpose）：运行 `ollama list`，获取已安装的模型列表
   - 子代理 2（general-purpose）：运行 `ollama ps`，获取当前正在运行的模型状态
3. 使用 wait_for_subagent 等待两个子代理全部完成
4. 汇总两个子代理的结果，整理为研究笔记
5. 撰写最终报告并保存到 /reports/ 目录

## 期望产出
- 已安装模型清单（ollama list）
- 当前运行中的模型及资源占用状态（ollama ps）
- 综合比较分析
