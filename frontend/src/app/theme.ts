import { createTheme } from "@mui/material/styles";
import Fade from "@mui/material/Fade";
import { tokens, hardShadow } from "../ui/tokens";

const print = tokens.color;

export const kigTheme = createTheme({
  palette: {
    mode: "light",
    common: {
      black: print.ink,
      white: print.creamPanel,
    },
    primary: {
      main: print.red,
      light: print.accentLight,
      dark: print.redDark,
      contrastText: print.contrast,
    },
    secondary: {
      main: print.inkBlue,
      light: print.secondaryLight,
      dark: print.secondaryDark,
      contrastText: print.creamPanel,
    },
    error: {
      main: print.error,
      light: print.errorLight,
      dark: print.errorDark,
      contrastText: print.contrast,
    },
    warning: {
      main: print.red,
      contrastText: print.contrast,
    },
    success: {
      main: print.green,
      contrastText: print.contrast,
    },
    background: {
      default: print.cream,
      paper: print.creamPanel,
    },
    text: {
      primary: print.ink,
      secondary: print.muted,
    },
    divider: print.line,
  },
  shape: {
    borderRadius: tokens.shape.borderRadius,
  },
  typography: tokens.typography,
  components: {
    MuiAlert: {
      styleOverrides: {
        root: {
          backgroundColor: print.creamPanel,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
          boxShadow: hardShadow(tokens.shadow.control),
          color: print.ink,
          fontFamily: "inherit",
        },
      },
    },
    MuiButton: {
      defaultProps: {
        disableElevation: true,
        size: "medium",
      },
      styleOverrides: {
        root: {
          borderRadius: tokens.shape.borderRadius,
          boxShadow: hardShadow(tokens.shadow.control),
          fontFamily: "inherit",
          fontWeight: tokens.weight.strong,
          lineHeight: 1.15,
          minWidth: 0,
          position: "relative",
          transition:
            `background-color ${tokens.motion.color}, border-color ${tokens.motion.color}, color ${tokens.motion.color}, transform ${tokens.motion.movement}, box-shadow ${tokens.motion.movement}`,
          whiteSpace: "nowrap",
          "&:hover": {
            boxShadow: hardShadow(tokens.shadow.raised),
            transform: tokens.motion.hover,
          },
          "&:active": {
            boxShadow: hardShadow(tokens.shadow.pressed),
            transform: tokens.motion.pressed,
          },
          "&.Mui-disabled": {
            boxShadow: "none",
            color: print.disabledText,
          },
        },
        contained: {
          backgroundColor: print.red,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          color: print.contrast,
          "&:hover": {
            backgroundColor: print.redDark,
          },
        },
        text: {
          color: print.ink,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          backgroundColor: print.chip,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
          color: print.ink,
          fontFamily: "inherit",
          fontWeight: tokens.weight.strong,
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          backgroundColor: print.creamPanel,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
          boxShadow: hardShadow(tokens.shadow.compact),
          color: print.ink,
          transition: `background-color ${tokens.motion.color}, color ${tokens.motion.color}, transform ${tokens.motion.movement}, box-shadow ${tokens.motion.movement}`,
          "&:hover": {
            backgroundColor: print.iconHover,
            boxShadow: hardShadow(tokens.shadow.control),
            transform: tokens.motion.hover,
          },
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          backgroundColor: print.progressTrack,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
        },
        bar: {
          backgroundColor: print.red,
        },
      },
    },
    MuiPopover: { defaultProps: { slots: { transition: Fade }, transitionDuration: { enter: 180, exit: 135 } } },
    MuiMenu: {
      defaultProps: { slots: { transition: Fade }, transitionDuration: { enter: 180, exit: 135 } },
      styleOverrides: {
        paper: {
          backgroundImage: "none",
          backgroundColor: print.creamPanel,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
          boxShadow: hardShadow(tokens.shadow.raised),
          color: print.ink,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundColor: print.creamPanel,
          backgroundImage: "none",
          borderRadius: tokens.shape.borderRadius,
        },
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: {
          color: print.red,
          height: 4,
        },
        rail: {
          backgroundColor: print.line,
          opacity: 1,
        },
        thumb: {
          backgroundColor: print.red,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: "50%",
          boxShadow: hardShadow(tokens.shadow.thumb),
          height: 18,
          width: 18,
          "&:hover, &.Mui-focusVisible": {
            boxShadow: hardShadow(tokens.shadow.compact),
          },
        },
        track: {
          backgroundColor: print.line,
          border: 0,
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          minHeight: 48,
          textTransform: "none",
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          "& .MuiFilledInput-root": {
            backgroundColor: print.panelSoft,
            border: `${tokens.shape.borderWidth}px solid ${print.line}`,
            borderRadius: tokens.shape.borderRadius,
            color: print.ink,
            fontFamily: "inherit",
            "&:before, &:after": {
              display: "none",
            },
            "&:hover": {
              backgroundColor: print.creamPanel,
              borderColor: print.line,
            },
            "&.Mui-focused": {
              borderColor: print.red,
              boxShadow: hardShadow(tokens.shadow.compact),
            },
            "&.Mui-disabled": {
              backgroundColor: print.disabledPanel,
            },
          },
          "& .MuiInputBase-input": {
            color: print.ink,
          },
          "& .MuiInputBase-input::placeholder": {
            color: print.muted,
            opacity: 1,
          },
          "& .MuiInputLabel-root": {
            color: print.muted,
          },
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: print.ink,
          border: `${tokens.shape.borderWidth}px solid ${print.line}`,
          borderRadius: tokens.shape.borderRadius,
          color: print.creamPanel,
          fontFamily: "inherit",
        },
      },
    },
  },
});
