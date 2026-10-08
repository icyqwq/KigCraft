"""Only text that the public message renderer exposes is searchable."""
from app.conversation.public_text import public_assistant_text


def message_search_text(role, parts):
    if role not in {"user", "assistant"}:
        return ""
    values = []
    for part in parts:
        kind = part.get("type")
        if kind == "text":
            value = part.get("text", "")
            if isinstance(value, str):
                values.append(public_assistant_text(value) if role == "assistant" else value)
        elif kind == "references":
            values.extend(item["usage"] for item in part.get("items", [])
                          if isinstance(item.get("usage"), str))
        elif kind == "annotations":
            values.extend(mark[key] for mark in part.get("annotations", []) for key in ("text", "note")
                          if isinstance(mark.get(key), str))
    return "\n".join(values)


def excerpt(value, query):
    index = value.lower().find(query.lower())
    start = max(0, index - 45)
    return ("…" if start else "") + value[start:start+180] + ("…" if len(value)>start+180 else "")
