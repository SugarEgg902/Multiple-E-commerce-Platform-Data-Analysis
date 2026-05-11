from pathlib import Path


def test_index_page_hides_static_marketing_copy():
    html = Path("frontend/index.html").read_text(encoding="utf-8")

    assert "Amazon Competitor Agent" not in html
    assert "Static Frontend" not in html
    assert "输入中文任务，后端负责抓取、分析、导出 CSV" not in html
    assert "POST 创建任务，SSE 持续推送状态" not in html
    assert "状态消息、逐项结果和最终 CSV 预览都会按顺序出现在这里。" not in html


def test_frontend_script_does_not_append_intro_message():
    script = Path("frontend/app.js").read_text(encoding="utf-8")

    assert 'title: "Ready"' not in script
    assert "提交任务后，这里会持续显示状态流、逐项完成消息和最终 CSV 预览。" not in script


def test_frontend_script_uses_session_routes_instead_of_chat_routes():
    script = Path("frontend/app.js").read_text(encoding="utf-8")

    assert 'fetch("/api/sessions"' in script
    assert "/messages" in script
    assert "/runs/" in script
    assert "/api/chat" not in script


def test_frontend_script_handles_mixed_agent_event_types():
    script = Path("frontend/app.js").read_text(encoding="utf-8")

    assert 'type === "assistant"' in script
    assert 'type === "tool_status"' in script
    assert 'type === "artifact"' in script
    assert 'type === "done"' in script


def test_index_page_uses_multi_turn_example_copy():
    html = Path("frontend/index.html").read_text(encoding="utf-8")

    assert "帮我看一下 Blackview 的竞品" in html
    assert "从亚马逊获取 Blackview 5 个竞品分析" not in html
