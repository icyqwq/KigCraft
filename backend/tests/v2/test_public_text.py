import pytest
from app.conversation.public_text import public_assistant_text
from app.design.models import Message
from app.design.router import message_json

@pytest.mark.parametrize("text", [
    r"[Generated image](C:\Users\test\generated\output.png)",
    "![image](sandbox:/mnt/data/result.png)",
    "Saved to `/tmp/codex/output.png`",
    "Saved to /home/test/output.png",
])
def test_local_transport_paths_are_removed(text):
    result=public_assistant_text(text)
    assert "output.png" not in result and "result.png" not in result
    assert "Users" not in result and "sandbox:" not in result

def test_public_links_and_design_text_are_preserved():
    value="Use a coral wig. [Reference](https://example.com/design)"
    assert public_assistant_text(value)==value

def test_projection_preserves_user_original_text_but_hides_empty_artifact_message():
    text=r"Reference filename C:\costume\coral.png"
    user=Message(id="u",role="user",content_parts=[{"type":"text","text":text}],status="completed")
    assert message_json(user)["content_parts"][0]["text"]==text
    assistant=Message(id="a",role="assistant",content_parts=[
        {"type":"text","text":r"[Generated image](C:\Users\test\out.png)"}],status="completed")
    assert message_json(assistant) is None
