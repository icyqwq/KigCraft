import pytest
from app.conversation.public_text import public_assistant_prefix

@pytest.mark.parametrize("value", [
    "Use coral. ![Generated image](C:/Users/private/image.png) More words here.",
    "Use coral. [Generated image](sandbox:/mnt/data/private.png) More words here.",
    "Use coral. `/home/private/image.png` More words here.",
    "Use coral. /workspace/private/image.png More words here.",
    "Use coral. [Generated image](C:/Users/Private Person/image.png) More words here.",
])
def test_every_transport_split_hides_local_path_fragments(value):
    for index in range(1,len(value)+1):
        result=public_assistant_prefix(value[:index])
        assert "private" not in result.lower()
        assert "Users/" not in result and "sandbox:/" not in result and "/home/" not in result
    assert public_assistant_prefix(value).startswith("Use coral.")

def test_chinese_punctuation_releases_complete_phrase_without_fake_typing():
    assert public_assistant_prefix("保留脸型，调整发色。接下来") == "保留脸型，调整发色。"
    assert public_assistant_prefix("Hello wor") == "Hello"
