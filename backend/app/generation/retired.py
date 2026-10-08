"""Retired execution identifiers remain readable in historical records only."""
RETIRED_PROVIDERS = frozenset({"qwen", "seedream", "qwen_codex", "qwen_seedream"})


class RetiredProviderError(ValueError):
    def __init__(self):
        super().__init__("legacy_provider_retired")


def reject_retired(provider, snapshot=None):
    values = [provider]
    if snapshot:
        values.extend(snapshot.get(key) for key in ("provider_preset", "analysis_provider", "image_provider"))
    if any(isinstance(value, str) and value in RETIRED_PROVIDERS for value in values):
        raise RetiredProviderError()
