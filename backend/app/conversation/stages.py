"""Fixed briefs for the guided first steps. The user text stays exactly as typed; the brief rides beside it."""
from __future__ import annotations

# How a head-shell image should look: a photograph of a physical object, not an illustration.
HEAD_SHELL_LOOK = (
    "Look: a photorealistic studio product photograph of a real, physical, hand-made kigurumi head shell wearing its wig, "
    "the kind of catalogue photo a professional maker shoots for a commission. Shot on a full-frame camera with an 85 mm "
    "lens at f/8, tack sharp across the face and fringe with only a gentle falloff toward the far hair ends, true colour, "
    "natural dynamic range with soft highlights and open shadows, fine real-world surface texture. "
    "It must not read as anime art, an illustration, a 3D render, a figure or a digital painting: no outlines or ink "
    "lines, no cel shading, no flat colour fills, no airbrushed gradients, no painted-on highlights, no glow or bloom. "
    "Anime proportions and the character's design stay, but everything is built from real materials with real depth. "
    "Studio set-up: a seamless light grey-white paper backdrop, a large soft box key light from the front left at about "
    "45 degrees, a softer fill from the right, a strip light or hair light from behind and above that rims the wig and "
    "separates it from the backdrop, and a faint kicker on the cheek. Light falls off naturally: the side of the face "
    "away from the key is a few stops darker, with soft form shadows under the brow, nose, lower lip, fringe and wig. "
    "Materials: the shell is a smooth hand-painted resin or fibreglass surface with a soft satin sheen, faint brush "
    "and sanding texture, and subtle sculpted relief at the brow, nose bridge, cheeks and lips, so the face is a real "
    "three-dimensional form lit from the side, not a flat drawing. The eyes are glossy clear-domed lens-like eyes set "
    "in sculpted sockets with printed or painted irises, a visible thickness and depth to the dome, sharp softbox "
    "reflections (catchlights) as rectangular window highlights, and a thin eyelid and lash edge casting a small shadow; "
    "never flat drawn eyes. Brows, lashes, blush and lips are matte paint sitting on the surface. The wig is "
    "heat-resistant synthetic fibre with a pronounced silky sheen: individual strands are visible, bright specular "
    "highlights run along the strands in soft bands that curve with the hair, the roots are darker and the layers "
    "underneath fall into shadow, there is natural volume, fine flyaways at the edges and a believable parting where the "
    "wig meets the shell; braids show the woven strand structure with a highlight on each ridge; the fringe casts a soft "
    "shadow on the forehead. Colours stay true to the character, rendered with real-world tonal range, soft contact "
    "shading between hair and face, and fine surface detail everywhere. "
)

# Shared by every head-shell image (render, edits and extra views) so they all hang the same way.
HEAD_SHELL_PRESENTATION = HEAD_SHELL_LOOK + (
    "Presentation: the head shell is photographed floating in mid-air, as if suspended by an invisible support. "
    "No stand, mannequin, neck, bust, table, floor, shelf or any other surface touches or supports it, and there is no "
    "floor or contact shadow beneath it (at most a faint soft shadow far behind it on the white backdrop). The shell is a "
    "hollow head: below the face it simply ends at its natural jaw and opening edge, with no neck stub and no base. "
    "The wig hangs freely under gravity: loose hair, side locks, braids, twin tails and ponytails fall straight down with "
    "natural weight, pass beyond the bottom of the shell through open air, and never rest on, bend against or pile up on "
    "any surface; long braids end in the air with their tips pointing down."
)

# Pose rule for the front image (flat reference and head-shell render). Alternate views state their own angle and do not use it.
HEAD_POSE = (
    "Head pose: the head is upright and faces the camera straight on, with no tilt, roll, turn or nod: the face's "
    "midline is vertical, the eyes and the mouth line are level, the chin is level, and the head is centred and "
    "symmetric. This holds even when a reference shows the character at an angle, tilted, turned, looking up or down "
    "or leaning: rebuild the head upright and frontal, inferring the side that was hidden conservatively from the "
    "visible hair and features, and never copy the reference's pose. The only exception is a turn that explicitly asks "
    "for another angle, such as a side, three-quarter or back view. "
)

FLAT_REFERENCE_BRIEF = (
    "Stage brief - clean flat head reference. Produce exactly ONE image: a clean character head design sheet that will "
    "later be used to build a kigurumi head shell. "
    "The user's reference images are the source of truth. Reproduce the character faithfully: head and face proportions, "
    "eye shape, iris colour and highlights, eyebrows, expression and temperament, hair colour and length, fringe and "
    "sidelocks, ahoge and every distinctive hair structure, ear shape, size, placement and colour, and the drawing style "
    "of the source. Do not invent features that are not in the references and do not average the character into a generic face. "
    "Remove everything that would interfere with building a head shell: background, body, clothing, props, hands, text, "
    "effects, and anything covering the head such as hoods, hats, veils or masks. Rebuild the hair and head shape those "
    "items hide so that it is consistent with the visible hair; infer hidden hair structure conservatively. Keep only the "
    "small accessories that belong to the character identity (hairpins, ribbons, earrings, small ornaments), placed where "
    "they sit on the head. "
    "Composition: front-facing, centred, the whole head including the full hair silhouette and ears "
    "visible; head and hair only (at most a short neck stub), no shoulders or body. " + HEAD_POSE +
    "Medium: keep the source rendering. For illustrated sources draw a flat 2D illustration with clean lines. Do not turn "
    "it into a realistic, 3D, doll, figure or cosplay image, do not moe-ify the face, and do not add 3D depth or realistic shading. "
    "Background: plain pure white with even lighting, no shadow, no frame, no labels. "
    "This is a reference image, not a head shell: draw no kigurumi shell, foam, seams, wig fibres or studio photograph. "
    "Declare image_kind=reference in the reply. If the user supplied no usable character reference at all, ask for one "
    "concise question; otherwise generate."
)
HEAD_SHELL_BRIEF = (
    "Stage brief - head-shell render. The deliverable is the realistic studio render of the physical head shell with its "
    "wig, translated from the character references. If the base image is a clean flat reference image, treat it as the "
    "authoritative design source to translate, not as an image to redraw. " + HEAD_POSE + HEAD_SHELL_PRESENTATION +
    " Declare image_kind=head_shell in the reply."
)

FOUR_VIEW_BRIEF = (
    "Stage brief - four-view sheet. Produce exactly ONE image: a horizontal character design sheet that shows the SAME "
    "physical kigurumi head shell with its wig from four directions, side by side in a single row, left to right: "
    "(1) front, facing the camera straight on; (2) front three-quarter, turned about 45 degrees to the character's left, "
    "so its face is still clearly visible; (3) side profile, turned 90 degrees; (4) back, turned 180 degrees, the back "
    "of the head and wig with no face visible. Within each view the head is upright, with no tilt, roll or nod; only the "
    "turn differs. The four heads are the same size and scale, evenly spaced, aligned on one common horizontal baseline, "
    "centred vertically, with clear margins around each and no overlap or cropping. "
    "The first attached image is the authoritative base: face, proportions, eyes, expression, hair colour, length, fringe, "
    "ahoge, side locks, ear shape, accessories and materials must be identical in all four heads. Any further attached "
    "images are extra references from the user for specific sides or details: follow each stated usage, never replace the "
    "base character's identity with theirs, never copy a reference as the output. Where the sides or the back are not shown "
    "anywhere, infer them conservatively from the base and the visible hair; do not invent new accessories or a different "
    "hairstyle. "
    "One shared plain white seamless backdrop and the same lighting direction across all four heads. Draw no text, labels, "
    "arrows, grid lines, panel borders, watermark or extra heads, and do not output four separate images. "
    + HEAD_SHELL_PRESENTATION + " Declare image_kind=head_shell in the reply."
)


def stage_brief(snapshot) -> str | None:
    """The brief for the turn target chosen by the user, or None for ordinary follow-up turns."""
    return {"reference": FLAT_REFERENCE_BRIEF, "head_shell": HEAD_SHELL_BRIEF, "four_view": FOUR_VIEW_BRIEF}.get(snapshot.target_kind)
