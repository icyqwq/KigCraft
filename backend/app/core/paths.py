from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return repo_root() / path
