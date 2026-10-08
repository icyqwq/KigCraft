import re
from pathlib import Path


FORBIDDEN_PATTERNS = [
    re.compile(r"premium", re.IGNORECASE),
    re.compile(r"normal user", re.IGNORECASE),
    re.compile(r"normal_user", re.IGNORECASE),
    re.compile(r"premium_user", re.IGNORECASE),
    re.compile(r"advanced user", re.IGNORECASE),
    re.compile(r"advanced_user", re.IGNORECASE),
    re.compile(r"高级用户"),
    re.compile(r"普通用户"),
    re.compile(r"normal_quota", re.IGNORECASE),
    re.compile(r"premium_quota", re.IGNORECASE),
    re.compile(r"advanced_quota", re.IGNORECASE),
    re.compile(r"normal_base_quota", re.IGNORECASE),
    re.compile(r"premium_base_quota", re.IGNORECASE),
    re.compile(r"advanced_base_quota", re.IGNORECASE),
    re.compile(r"""["']normal["']""", re.IGNORECASE),
    re.compile(r"""["']advanced["']""", re.IGNORECASE),
]


def test_forbidden_patterns_match_bare_tier_string_literals() -> None:
    assert any(pattern.search('tier = "normal"') for pattern in FORBIDDEN_PATTERNS)
    assert any(pattern.search("tier = 'advanced'") for pattern in FORBIDDEN_PATTERNS)


def test_forbidden_patterns_do_not_flag_normal_prose() -> None:
    text = 'prompt = "Allow normal anime references when the head is visible."'

    assert not any(pattern.search(text) for pattern in FORBIDDEN_PATTERNS)


def test_public_api_files_do_not_expose_tier_words() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    public_files = [
        path
        for path in app_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and path.relative_to(app_root).parts[0]
        in {"admin", "album", "audit", "auth", "generation", "prompts", "references"}
    ]

    assert public_files
    for path in public_files:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            assert not pattern.search(text), f"{pattern.pattern} leaked in {path.relative_to(app_root)}"
