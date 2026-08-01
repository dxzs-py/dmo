"""诊断 LangGraph Interrupt 对象结构与 astream updates 中 __interrupt__ 的实际格式。"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

import inspect

from langgraph.types import Interrupt

print("=== langgraph.types.Interrupt 类结构 ===")
print(f"Interrupt: {Interrupt}")
print(f"fields: {getattr(Interrupt, '__fields__', 'N/A')}")
sig = inspect.signature(Interrupt.__init__) if hasattr(Interrupt, "__init__") else "N/A"
print(f"__init__ sig: {sig}")
# 打印实例属性（构造一个示例）
try:
    sample = Interrupt(value={"_approval": True}, id="test-id-123")
    print(f"sample.value={sample.value!r}")
    print(f"sample.id={getattr(sample, 'id', 'NO id attr')!r}")
    print(f"sample attrs={[a for a in dir(sample) if not a.startswith('_')]}")
except Exception as e:
    print(f"构造示例失败: {e}")

print()
print("=== langgraph 版本 ===")
import langgraph

print(f"langgraph version: {getattr(langgraph, '__version__', 'unknown')}")

# 检查 checkpointer 中某会话的 pending interrupt id
print()
print("=== 检查最近会话的 pending interrupt（从 checkpointer）===")
import asyncio

from Django_xm.apps.chat.services.chat_service import ChatService


async def inspect_pending_interrupt():
    from Django_xm.apps.approvals.models import Approval

    # 找一个最近 processing/waiting 状态的 chat 审批
    ap = (
        Approval.objects.filter(source="chat", state__in=["processing", "waiting", "pending"])
        .order_by("-created_at")
        .first()
    )
    if not ap:
        print("无 pending/processing/waiting chat 审批")
        return
    print(f"审批: id={ap.id} interrupt_id={ap.interrupt_id} state={ap.state}")
    print(f"  source_id={ap.source_id} (session)")
    extra = ap.extra if isinstance(ap.extra, dict) else {}
    print(f"  extra.graph_interrupt_id={extra.get('graph_interrupt_id')!r}")
    print(f"  extra.langgraph_resume_id={extra.get('langgraph_resume_id')!r}")
    session_id = ap.source_id
    try:
        chat_service = ChatService(user_id=ap.user_id, thread_id=session_id)
        data = {"session_id": session_id, "mode": "agent"}
        model_instance = ChatService._resolve_model_instance(data)
        tools = await chat_service._get_tools(data)
        agent, thread_config, _use_checkpointer = await chat_service._create_agent_with_memory(
            data,
            prompt_mode="agent",
            model_instance=model_instance,
            tool_config=chat_service._build_tool_config(data),
            tools=tools,
        )
        graph = agent.graph if hasattr(agent, "graph") else agent
        if hasattr(graph, "aget_state"):
            st = await graph.aget_state(thread_config)
            print(f"  check_state.tasks count={len(st.tasks) if st and st.tasks else 0}")
            if st and st.tasks:
                for ti, task in enumerate(st.tasks):
                    intrs = getattr(task, "interrupts", None) or []
                    print(f"    task[{ti}] interrupts={len(intrs)}")
                    for ii, intr in enumerate(intrs):
                        intr_id = getattr(intr, "id", "NO_ID_ATTR")
                        intr_value = getattr(intr, "value", "NO_VALUE_ATTR")
                        print(f"      intr[{ii}].id={intr_id!r} (type={type(intr_id).__name__})")
                        print(f"      intr[{ii}].value type={type(intr_value).__name__}")
                        if isinstance(intr_value, dict):
                            print(f"      intr[{ii}].value keys={list(intr_value.keys())}")
        else:
            print("  graph 无 aget_state")
    except Exception as e:
        print(f"  检查失败: {e!r}")
    finally:
        try:
            from Django_xm.apps.agent_hub.checkpointing.checkpointer_factory import release_async_checkpointer

            await release_async_checkpointer()
        except Exception:  # noqa: S110
            pass


asyncio.run(inspect_pending_interrupt())
