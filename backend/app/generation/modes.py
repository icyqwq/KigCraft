from typing import Literal, cast


GenerationMode = Literal[
    "front_design",
    "front_revision",
    "front_local_revision",
    "turnaround",
]

DEFAULT_GENERATION_MODE: GenerationMode = "front_design"
AI_OUTPUT_LANDMARKS_ENABLED = False
VALID_GENERATION_MODES = {
    "front_design",
    "front_revision",
    "front_local_revision",
    "turnaround",
}


def normalize_generation_mode(value: str | None) -> GenerationMode:
    if value in VALID_GENERATION_MODES:
        return cast(GenerationMode, value)
    return DEFAULT_GENERATION_MODE


def expected_output_count(generation_mode: str | None) -> int:
    normalize_generation_mode(generation_mode)
    return 1


def expected_output_indexes(generation_mode: str | None) -> list[int]:
    return list(range(1, expected_output_count(generation_mode) + 1))

