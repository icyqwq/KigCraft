from app.generation.usage import (
    extract_token_usage_from_codex_events,
    merge_token_usage,
    parse_token_usage,
)


def test_extract_token_usage_from_codex_events_merges_usage_lines():
    usage = extract_token_usage_from_codex_events(
        b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":70,"output_tokens":9,"reasoning_output_tokens":2}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":40,"cached_input_tokens":20,"output_tokens":5,"reasoning_output_tokens":1}}\n'
    )

    assert usage is not None
    assert usage.to_dict() == {
        "input_tokens": 140,
        "cached_input_tokens": 90,
        "output_tokens": 14,
        "reasoning_output_tokens": 3,
        "total_tokens": 154,
    }


def test_parse_token_usage_accepts_openai_style_usage_details():
    usage = parse_token_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 4},
        }
    )

    assert usage is not None
    assert usage.to_dict() == {
        "input_tokens": 100,
        "cached_input_tokens": 30,
        "output_tokens": 20,
        "reasoning_output_tokens": 4,
        "total_tokens": 120,
    }


def test_merge_token_usage_ignores_missing_values():
    usage = merge_token_usage(
        parse_token_usage({"input_tokens": 10}),
        parse_token_usage({"output_tokens": 5}),
    )

    assert usage is not None
    assert usage.to_dict() == {
        "input_tokens": 10,
        "cached_input_tokens": None,
        "output_tokens": 5,
        "reasoning_output_tokens": None,
        "total_tokens": 15,
    }
