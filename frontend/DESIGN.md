# KigCraft print workbench

The current interface uses warm paper surfaces, dark ink, coral actions, square
panels and hard offset shadows. Preserve this visual language while implementing
the V2 conversation and editor workflows.

## Shared source

Edit `src/ui/tokens.ts` for shared colors, locale fonts, typography, borders,
shadows and interaction timing. MUI consumes it directly in `src/app/theme.ts`.
The same source generates `src/ui/tokens.css`, imported by `src/styles/index.css`.

Run `npm run tokens:generate` after editing tokens and include the generated CSS
in the change. The Vite development server generates it on startup and token
edits. Both `npm run build` and `npm test` check for stale CSS and fail with the
regeneration command. No additional runtime dependency is required.

New shared UI styling should use tokens or the existing CSS custom properties.
Domain colors inside edited images, masks and exported annotations are separate
from the interface palette. Existing component-local declarations can migrate
incrementally; the token extraction does not redefine their appearance.

## Color and type

- Page and panel: `color.cream`, `color.creamPanel`, `color.panelSoft`.
- Text and borders: `color.ink`, `color.muted`, `color.line`.
- Actions: `color.red`; hover: `color.redDark`; status colors have dedicated tokens.
- CSS `--kb-muted` remains translucent ink, distinct from MUI secondary text.
- Default font: the existing Chinese serif stack, with Times New Roman fallback.
  Japanese uses its own serif stack through `html[data-locale="ja"]`.
- MUI base size remains 15px; page body remains 1rem. Strong labels use weight 900.
  Exported annotations retain their own image-space font rules.

Legacy names remain compatible: `--kb-dirty-yellow`, `--kb-muted-red` and
`--kb-coral` all retain the current coral action color. Their historical names
do not indicate a yellow palette or the error color.

## Surfaces and interaction

Panels and controls use square corners and 2px ink borders. Slider thumbs remain
round. Shadows are hard, without blur: the shared offsets include 8px panels,
5px controls, 4px compact controls, 6px raised and 2px pressed states.
The legacy `--kb-hard-shadow-sm` stays at 5px.

Buttons move by -1px on hover and 2px when pressed. Color transitions remain
140ms; movement and shadow transitions remain 120ms. Keep existing focus,
disabled behavior when changing shared styling. Shared CSS stops decorative
animations, control lift and smooth scrolling under reduced motion, while keeping
image geometry and viewport transforms. MUI keyboard focus has a visible ink
outline. A preference change must preserve drafts and control values.

Paper texture belongs behind page content. It must not tint images, canvases,
exported files or foreground controls. Keep the body stacking context and the
background-only grain layer.

## Layout and verification

Use constrained Grid/Flex layouts and responsive collapse. Avoid horizontal
overflow at 360, 390, 768, 1024 and 1440px; check Chinese, English and Japanese.
Keep decorative styling outside the image editing surface.

`e2e/tests/editor/design-tokens.spec.ts` compares the shared theme against the
immutable task-start theme, checks legacy CSS properties and compares rendered
controls in default, hover, active and focus states. The canvas-color suite
checks real editor screenshots against an independent color chart. These checks
do not replace the remaining full-product visual and accessibility acceptance.

Reference-image availability and visual evidence are tracked in
`docs/design/kigcraft-v2-ui-reference-index.md` at the repository root.

## Editor dependency boundary

The editor core, renderer, detection service and EditorWorkspace must not import
business API, authentication, design or account modules, including through type
imports, re-exports and lazy components. Pass business actions as typed callbacks;
Composer injects the account materials picker into ReferenceAttachments.
Cloud draft wire/state types live in drafts/cloudTypes.ts, separately from HTTP.

`npm run boundaries:check` follows project source imports and rejects new explicit
`any` or TypeScript suppression comments in production code. The four existing
MUI compatibility assertions are capped in their original file. Build and test
commands run this check; `node --test scripts/editor-boundaries.test.mjs` verifies
the checker. TypeScript strict checking remains required. This source guard does
not replace runtime request validation or backend authorization.
