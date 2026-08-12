# 研究计划：LangGraph 的 interrupt 机制

## 研究目标
简要说明 LangGraph 中 interrupt 机制的概念、作用、工作原理和典型使用场景。

## 研究步骤
1. 网络搜索：搜集 LangGraph interrupt 机制的官方文档和社区资料
   - 关键词：LangGraph interrupt mechanism、human-in-the-loop、interrupt_before/interrupt_after、动态中断
2. 整理搜索结果，提取关键概念：
   - interrupt 是什么（静态 vs 动态）
   - 与 human-in-the-loop 的关系
   - 工作原理（节点执行中断、状态保存、从断点恢复）
   - 典型使用场景（人工审批、用户输入收集、多智能体协调）
   - 相关 API：interrupt_before / interrupt_after / interrupt() / Command(resume=)
3. 将研究笔记写入 /notes/
4. 整合信息，撰写最终报告至 /reports/

## 输出
- /notes/web_research.md — 网络研究笔记
- /reports/final_report.md — 最终研究报告（简要说明）
