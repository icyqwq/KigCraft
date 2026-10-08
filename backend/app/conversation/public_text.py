"""Keep local artifact transport paths out of user-facing assistant messages."""
import re

_LOCAL_START = re.compile(r"^(?:[A-Za-z]:[\\/]|file:|sandbox:|/(?:tmp|mnt|home|Users|var|workspace)/)", re.I)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^\n]*?)\)")
_CODE_PATH = re.compile(r"`((?:[A-Za-z]:[\\/]|file:|sandbox:|/(?:tmp|mnt|home|Users|var|workspace)/)[^`\n]*)`", re.I)
_BARE_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|file://|sandbox:/|/(?:tmp|mnt|home|Users|var|workspace)/)[^\s<>\"'`]+", re.I)


def public_assistant_prefix(value: str) -> str:
    """Hold incomplete lexical/markdown fragments until they can be redacted safely."""
    # An unfinished transport link or code span must never expose its destination.
    limit = len(value)
    bracket = value.rfind("[")
    if bracket >= 0:
        close = value.find("]", bracket)
        if close < 0 or close == len(value) - 1 or (
                value[close + 1:close + 2] == "(" and value.find(")", close) < 0):
            limit = min(limit, bracket - (1 if bracket and value[bracket - 1] == "!" else 0))
    if value.count("`") % 2:
        limit = min(limit, value.rfind("`"))
    prefix = public_assistant_text(value[:limit])
    boundaries = list(re.finditer(r"[\s。！？；，]", prefix))
    if not boundaries:
        return ""
    return public_assistant_text(prefix[:boundaries[-1].end()])


def public_assistant_text(value: str) -> str:
    # Native image.ready cards replace these transport links. Never apply to user text.
    value = _MARKDOWN_LINK.sub(lambda match: "" if _LOCAL_START.match(
        match.group(1).strip().lstrip("<")) else match.group(0), value)
    value = _CODE_PATH.sub("", value)
    value = _BARE_PATH.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()
