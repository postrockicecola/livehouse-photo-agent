from services.agent.context_governance import (
    compress_working_memory,
    truncate_tool_observation,
    working_memory_prompt_block,
)
from services.agent.conversation import ConversationMemory


def test_truncate_tool_observation():
    big = "x" * 10_000
    out = truncate_tool_observation(big, max_chars=100)
    assert len(out) < 200
    assert "truncated" in out


def test_conversation_memory_truncates_tool_results():
    mem = ConversationMemory(system_prompt="sys", max_tokens=50_000)
    mem.add_tool_result("gallery_search", "y" * 20_000, max_chars=500)
    tool_msgs = [m for m in mem.messages() if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert len(tool_msgs[0]["content"]) < 600


def test_working_memory_prompt_block():
    wm = compress_working_memory(
        {
            "last_query": "鼓手",
            "last_tool": "gallery_search",
            "last_files": ["a.jpg", "b.jpg"],
            "last_citations": [{"file": "a.jpg", "caption": "hit", "fused_score": 0.9}],
        }
    )
    block = working_memory_prompt_block(wm)
    assert "鼓手" in block
    assert "a.jpg" in block
    assert "NEVER ask the user to re-list filenames" in block


def test_compress_working_memory_keeps_forty_files():
    files = [f"img_{i:03d}.jpg" for i in range(40)]
    wm = compress_working_memory({"last_files": files, "last_tool": "gallery_search"})
    assert len(wm["last_files"]) == 40
    block = working_memory_prompt_block(wm)
    assert "img_000.jpg" in block and "img_039.jpg" in block


def test_active_task_is_compact_and_prompted_for_cross_turn_resume():
    wm = compress_working_memory(
        {
            "active_task": {
                "active_goal": "选出20张演出照片并导出",
                "last_request": "先找主唱和吉他手",
                "status": "in_progress",
                "steps": [
                    {"id": "step_1", "tool": "gallery_search", "status": "completed"},
                    {"id": "step_2", "tool": "gallery_select", "status": "pending"},
                ],
                "completed_steps": ["step_1"],
                "constraints": {"limit": 20, "subject": "主唱和吉他手"},
                "last_error": "",
            }
        }
    )

    task = wm["active_task"]
    assert task["schema_version"] == "gallery_task.v1"
    assert task["completed_steps"] == ["step_1"]
    block = working_memory_prompt_block(wm)
    assert "选出20张演出照片并导出" in block
    assert "gallery_select [pending]" in block
    assert "do not make them restate the original goal" in block
