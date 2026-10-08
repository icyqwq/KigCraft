import copy
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.design.editor_document import EditorDocument

CASES = json.loads((Path(__file__).resolve().parents[3] / "docs/design/fixtures/v2-editor-document.json").read_text(encoding="utf-8"))

def altered(case):
    value = copy.deepcopy(CASES["valid"][-1]["document"])
    target = value
    for key in case["path"][:-1]:
        target = target[key]
    if case.get("remove"):
        del target[case["path"][-1]]
    else:
        target[case["path"][-1]] = case["value"]
    return value

@pytest.mark.parametrize("case", CASES["valid"], ids=lambda c: c["name"])
def test_valid_editor_document_preserves_wire_values(case):
    original = copy.deepcopy(case["document"])
    parsed = EditorDocument.model_validate(case["document"])
    assert parsed.model_dump(mode="json") == original
    assert case["document"] == original

@pytest.mark.parametrize("case", CASES["invalid"], ids=lambda c: c["name"])
def test_invalid_nested_document_is_rejected(case):
    with pytest.raises(ValidationError):
        EditorDocument.model_validate(altered(case))

@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_recipe_values_are_rejected(number):
    value = copy.deepcopy(CASES["valid"][0]["document"])
    value["normalizedRecipe"]["face"]["faceWidth"] = number
    with pytest.raises(ValidationError):
        EditorDocument.model_validate(value)
