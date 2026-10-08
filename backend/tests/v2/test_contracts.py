import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.contracts.v2 import InputSnapshot, SubmitTurn, V2Capabilities

FIXTURES = Path(__file__).resolve().parents[3] / "docs/design/fixtures/v2-submit-turn.json"
CASES = json.loads(FIXTURES.read_text(encoding="utf-8"))

@pytest.mark.parametrize("payload", CASES["valid"])
def test_valid_shared_request(payload):
    parsed = SubmitTurn.model_validate(payload)
    assert parsed.text == payload["text"]

@pytest.mark.parametrize("payload", CASES["invalid"])
def test_invalid_shared_request(payload):
    with pytest.raises(ValidationError):
        SubmitTurn.model_validate(payload)

def test_snapshot_digest_is_stable_and_changes_with_input():
    snapshot = InputSnapshot(user_text="original", base_revision_id=None, branch_version=0, references=())
    assert snapshot.digest() == InputSnapshot.model_validate_json(snapshot.model_dump_json()).digest()
    assert snapshot.digest() != snapshot.model_copy(update={"user_text": "new"}).digest()

def test_unverified_capabilities_default_off():
    result = V2Capabilities().model_dump()
    assert not any(result[k] for k in ("enabled", "new_operations", "discussion", "generation", "history_fork", "viewsets"))
