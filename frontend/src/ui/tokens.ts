// Shared source for MUI and generated CSS. Run npm run tokens:generate after edits.
export const tokens = {
  color: {
    cream: "#f3ead7",
    creamDeep: "#e5d6bd",
    creamPanel: "#fff4dd",
    ink: "#191f23",
    inkBlue: "#182635",
    line: "#22282c",
    muted: "#5f5a50",
    red: "#c9552f",
    redDark: "#9d3a20",
    green: "#516a45",
    accentLight: "#de6a42",
    contrast: "#fff7ea",
    secondaryLight: "#324456",
    secondaryDark: "#0f1823",
    error: "#b6372b",
    errorLight: "#d45d4f",
    errorDark: "#84261f",
    disabledText: "rgba(25, 31, 35, 0.36)",
    chip: "#efe1c5",
    iconHover: "#f1ddbd",
    progressTrack: "#ddc7a3",
    panelSoft: "#fff8e9",
    disabledPanel: "#ead9bb",
    fadedInk: "#2e3437",
    teal: "#0d756f",
    concrete: "#6d675d",
    mutedOverlay: "rgba(25, 31, 35, 0.42)",
    panelHover: "#f2e2c6",
    buttonHover: "#b94728",
    lineSoft: "rgba(25, 31, 35, 0.18)",
    scrim: "rgba(25, 31, 35, 0.3)"
  },
  font: {
    default: "\"Noto Serif SC\", \"Source Han Serif SC\", \"Songti SC\", SimSun, \"Times New Roman\", serif",
    ja: "\"Noto Serif JP\", \"Hiragino Mincho ProN\", \"Yu Mincho\", YuMincho, serif"
  },
  shape: { borderRadius: 0, borderWidth: 2 },
  weight: { strong: 900, regular: 400, medium: 500, semibold: 600, bold: 700 },
  zIndex: { content: 0, sticky: 10, drawer: 1200, modal: 1300, toast: 1400 },
  shadow: { panel: 8, control: 5, compact: 4, raised: 6, pressed: 2, thumb: 3 },
  motion: {
    color: "140ms ease", movement: "120ms ease", hover: "translate(-1px, -1px)", pressed: "translate(2px, 2px)",
    fast: "120ms", base: "180ms", slow: "240ms", emphasis: "320ms",
    easeOut: "cubic-bezier(.2,.8,.2,1)", easeIn: "cubic-bezier(.4,0,1,1)",
  },
  typography: {
    fontFamily: "var(--kb-font-family)",
    fontSize: 15,
    button: {
      fontWeight: 900,
      letterSpacing: 0,
      textTransform: "none",
    },
    h1: {
      fontSize: "2rem",
      fontWeight: 900,
      letterSpacing: 0,
      lineHeight: 1.15,
    },
    h2: {
      fontSize: "1.5rem",
      fontWeight: 900,
      letterSpacing: 0,
      lineHeight: 1.2,
    },
    h3: {
      fontSize: "1.25rem",
      fontWeight: 900,
      letterSpacing: 0,
      lineHeight: 1.2,
    },
    h4: {
      fontSize: "1.125rem",
      fontWeight: 900,
      letterSpacing: 0,
      lineHeight: 1.2,
    },
    h5: {
      fontSize: "1rem",
      fontWeight: 900,
      letterSpacing: 0,
      lineHeight: 1.2,
    },
    body1: {
      lineHeight: 1.6,
    },
    body2: {
      lineHeight: 1.55,
    },
  },
} as const;

export function hardShadow(offset: number, color: string = tokens.color.inkBlue): string {
  return `${offset}px ${offset}px 0 ${color}`;
}

const c = tokens.color;
export const cssVariables = {
  "--kb-font-family": tokens.font.default,
  "--kb-faded-black": c.fadedInk,
  "--kb-paper-black": c.ink,
  "--kb-paper-dark": c.creamPanel,
  "--kb-old-paper": c.cream,
  "--kb-old-paper-2": c.creamDeep,
  "--kb-ink": c.ink,
  "--kb-dirty-yellow": c.red,
  "--kb-muted-red": c.red,
  "--kb-coral": c.red,
  "--kb-teal": c.teal,
  "--kb-off-white": c.contrast,
  "--kb-olive-drab": c.green,
  "--kb-denim-blue": c.inkBlue,
  "--kb-concrete-grey": c.concrete,
  "--kb-line": c.line,
  "--kb-line-strong": c.ink,
  "--kb-shadow": c.inkBlue,
  "--kb-accent": c.red,
  "--kb-text": c.ink,
  "--kb-muted": c.mutedOverlay,
  "--kb-panel": c.creamPanel,
  "--kb-panel-soft": c.panelSoft,
  "--kb-panel-hover": c.panelHover,
  "--kb-button-hover": c.buttonHover,
  "--kb-danger": c.error,
  "--kb-line-soft": c.lineSoft,
  "--kb-scrim": c.scrim,
  "--kb-motion-fast": tokens.motion.fast,
  "--kb-motion-base": tokens.motion.base,
  "--kb-motion-slow": tokens.motion.slow,
  "--kb-motion-emphasis": tokens.motion.emphasis,
  "--kb-ease-out": tokens.motion.easeOut,
  "--kb-ease-in": tokens.motion.easeIn,
  "--kb-z-content": String(tokens.zIndex.content),
  "--kb-z-sticky": String(tokens.zIndex.sticky),
  "--kb-z-drawer": String(tokens.zIndex.drawer),
  "--kb-z-modal": String(tokens.zIndex.modal),
  "--kb-z-toast": String(tokens.zIndex.toast),
  "--kb-weight-regular": String(tokens.weight.regular),
  "--kb-weight-medium": String(tokens.weight.medium),
  "--kb-weight-semibold": String(tokens.weight.semibold),
  "--kb-weight-bold": String(tokens.weight.bold),
  "--kb-hard-shadow-md": hardShadow(tokens.shadow.compact, "var(--kb-shadow)"),
  "--kb-hard-shadow": hardShadow(tokens.shadow.panel, "var(--kb-shadow)"),
  "--kb-hard-shadow-sm": hardShadow(tokens.shadow.control, "var(--kb-shadow)"),
} as const;

export function renderTokenCss(): string {
  const declarations = Object.entries(cssVariables).map(([name, value]) => `  ${name}: ${value};`).join("\n");
  return `/* Generated from src/ui/tokens.ts; run npm run tokens:generate. */\n:root {\n${declarations}\n}\n\nhtml[data-locale="ja"] {\n  --kb-font-family: ${tokens.font.ja};\n}\n`;
}
