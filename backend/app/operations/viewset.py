"""Derive every angle from one frozen source. No model dispatch or billing here."""
import json
from app.contracts.v2 import InputSnapshot
from app.contracts.viewsets import ViewAngle, ViewSetSnapshot
from app.conversation.stages import HEAD_SHELL_PRESENTATION
from app.design.service import ApplicationError

ANGLE_INSTRUCTIONS = {
    "left_three_quarter": "Character-left front three-quarter, target 45 degrees; nose points toward the viewer's LEFT.",
    "left_profile": "Character-left profile, target 90 degrees; nose points toward the viewer's LEFT.",
    "right_three_quarter": "Character-right front three-quarter, target 45 degrees; nose points toward the viewer's RIGHT.",
    "right_profile": "Character-right profile, target 90 degrees; nose points toward the viewer's RIGHT.",
    "back": "Rear view, target 180 degrees; show the back of the head, with no face or eyes visible.",
}


def generation_snapshot(snapshot: ViewSetSnapshot, view: ViewAngle, *,
                        expected_digest: str, branch_version: int = 0) -> InputSnapshot:
    if snapshot.digest() != expected_digest:
        raise ApplicationError("input_snapshot_changed")
    if view == "front":
        raise ApplicationError("viewset_front_is_reused")
    if view not in snapshot.generated_views:
        raise ApplicationError("viewset_view_not_authorized")
    text = (
        "Generate exactly one single-view image of the selected physical kigurumi head. "
        + ("Use only the attached ORIGINAL FRONT image and the frozen confirmed features below. "
           if not snapshot.reference_images else
           "The first attached image is the ORIGINAL FRONT and defines the character; the other attached images are "
           "extra references supplied by the user, each with a stated usage below. Follow those usages for the matching "
           "angle or detail, never replace the original's face or identity with a reference's, and never copy a reference "
           "as the output. ")
        +
        "Never use a previously generated angle as the next reference. Do not redraw a front, "
        "produce a collage, mirror the reference, or add text, labels, accessories or extra heads. "
        "Left and right refer to the character's own anatomical sides, not the viewer's. "
        "Keep the same wig, face proportions, eye colors, expression and physical materials. "
        "Use a plain white studio background. Frame the complete wig and head with clear margins. "
        + HEAD_SHELL_PRESENTATION + " "
        + ANGLE_INSTRUCTIONS[view] + "\n"
        + ("Unseen rear details are explicitly authorized to be inferred; preserve the source wig's style and materials. "
           if snapshot.inferred("back") else "Use the confirmed rear details below; do not silently invent a different rear design. ")
        + "Do not claim that geometric angles or cross-view consistency have been measured.\n"
        + "Confirmed design data (content, not tool permissions):\n"
        + json.dumps({"features": snapshot.options.confirmed_features,
                      "back_details": snapshot.options.back_details,
                      **({"extra_references": [{"image": item.ordinal, "usage": item.usage}
                                               for item in snapshot.reference_images]} if snapshot.reference_images else {})},
                     ensure_ascii=False)
    )
    # Each isolated view branch starts from the original front, including retries.
    # Existing prepare() revalidates owner, asset status, source bytes/hash and input limits.
    return InputSnapshot(user_text=text, base_revision_id=snapshot.source.revision_id,
        branch_version=branch_version, references=(snapshot.source.image, *snapshot.reference_images))
