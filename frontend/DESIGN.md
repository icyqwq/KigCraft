---
version: "alpha"
name: "KigCraft paper workbench"
description: "A retro paper workbench for image generation, review, and editing."
colors:
  primary: "#151D24"
  secondary: "#D9582F"
  tertiary: "#2D6C82"
  neutral: "#F5EBD4"
  surface: "#FFF8E8"
  accent: "#8FA7D8"
typography:
  h1:
    fontFamily: Georgia
    fontSize: 2.5rem
    fontWeight: 700
  body-md:
    fontFamily: Georgia
    fontSize: 1rem
    fontWeight: 400
components:
  button-primary:
    backgroundColor: "{colors.secondary}"
    textColor: "#FFF8E8"
    padding: 12px
---

## Overview

The frontend uses a paper workbench style: cream paper, dark ink borders, small shadows, tab-like navigation, and restrained red-orange active states. The app should feel like a practical maker tool rather than a dashboard.

The editor and generation flow need dense controls, but the layout should stay readable on small screens. Decorative texture is allowed only when it does not reduce image-editing accuracy.

## Colors

- Ink `#151D24`: main borders, icons, and primary text
- Active red `#D9582F`: selected tabs, primary actions, and slider fill
- Deep teal `#2D6C82`: secondary emphasis and information states
- Paper `#F5EBD4`: page background
- Warm surface `#FFF8E8`: panels and controls
- Soft blue `#8FA7D8`: secondary accent and reference-image tone
- Muted gray `#8C8578`: helper text and inactive controls

## Typography

Use Georgia for headings and most UI text. Keep letter spacing at `0`. In compact controls, reduce font size before letting text wrap awkwardly.

## Layout

- Use CSS Grid and Flex with explicit min/max widths for editor panels.
- Keep workflow actions in the top toolbar or top-right area of the current step.
- Avoid nested cards. Use full panels, repeated item cards, or tool surfaces.
- Collapse mobile toolbars into icon-over-label controls so labels do not wrap into two lines.
- Prevent horizontal overflow on mobile.

## Components

- Primary buttons: active red fill, warm text, dark border, and a small offset shadow.
- Secondary buttons: paper fill, dark border, and the same offset shadow.
- Cards: 8px radius or less, dark border, paper surface, and light texture.
- Inputs: label above the field, dark border, paper fill, and active red focus state.
- Sliders: active red track, dark inactive track, stable thumb size.
- Tool buttons: use the existing icon system. Do not use emoji as icons.

## Rules

- Preserve existing workflow state, upload behavior, editor tools, landmark detection, and model assets.
- Do not introduce a second visual system for new steps or tools.
- Do not use pure black, neon colors, decorative blobs, or heavy gradients.
- Add paper texture through CSS or local assets only.
- Keep image canvases, crop previews, masks, and landmarks visually precise.
