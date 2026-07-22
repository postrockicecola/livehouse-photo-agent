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
