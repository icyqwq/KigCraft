import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "total_tokens": self.total_tokens,
        }

    def has_values(self) -> bool:
        return any(value is not None for value in self.to_dict().values())


def parse_token_usage(value: Any) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None

    input_tokens = _int_value(value.get("input_tokens"))
    if input_tokens is None:
        input_tokens = _int_value(value.get("prompt_tokens"))

    output_tokens = _int_value(value.get("output_tokens"))
    if output_tokens is None:
        output_tokens = _int_value(value.get("completion_tokens"))

    cached_input_tokens = _int_value(value.get("cached_input_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _int_value(value.get("cached_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _nested_int_value(
            value, ("input_tokens_details", "cached_tokens")
        )
    if cached_input_tokens is None:
        cached_input_tokens = _nested_int_value(
            value, ("prompt_tokens_details", "cached_tokens")
        )

    reasoning_output_tokens = _int_value(value.get("reasoning_output_tokens"))
    if reasoning_output_tokens is None:
        reasoning_output_tokens = _int_value(value.get("reasoning_tokens"))
    if reasoning_output_tokens is None:
        reasoning_output_tokens = _nested_int_value(
            value, ("output_tokens_details", "reasoning_tokens")
        )
    if reasoning_output_tokens is None:
        reasoning_output_tokens = _nested_int_value(
            value, ("completion_tokens_details", "reasoning_tokens")
        )

    total_tokens = _int_value(value.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    usage = TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
    )
    return usage if usage.has_values() else None


def merge_token_usage(*usages: TokenUsage | None) -> TokenUsage | None:
    concrete_usages = [usage for usage in usages if usage and usage.has_values()]
    if not concrete_usages:
        return None

    input_tokens = _sum_optional(usage.input_tokens for usage in concrete_usages)
    cached_input_tokens = _sum_optional(
        usage.cached_input_tokens for usage in concrete_usages
    )
    output_tokens = _sum_optional(usage.output_tokens for usage in concrete_usages)
    reasoning_output_tokens = _sum_optional(
        usage.reasoning_output_tokens for usage in concrete_usages
    )
    total_tokens = _sum_optional(usage.total_tokens for usage in concrete_usages)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        total_tokens=total_tokens,
    )


def extract_token_usage_from_codex_events(data: bytes | str) -> TokenUsage | None:
    text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else data
    usages: list[TokenUsage] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        usage = parse_token_usage(event.get("usage") if isinstance(event, dict) else None)
        if usage is not None:
            usages.append(usage)
    return merge_token_usage(*usages)


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    return None


def _nested_int_value(value: dict[str, Any], path: tuple[str, str]) -> int | None:
    parent = value.get(path[0])
    if not isinstance(parent, dict):
        return None
    return _int_value(parent.get(path[1]))


def _sum_optional(values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None
