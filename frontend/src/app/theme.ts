import { createTheme } from "@mui/material/styles";

const print = {
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
};

export const kigTheme = createTheme({
  palette: {
    mode: "light",
    common: {
      black: print.ink,
      white: print.creamPanel,
    },
    primary: {
      main: print.red,
      light: "#de6a42",
      dark: print.redDark,
      contrastText: "#fff7ea",
    },
    secondary: {
      main: print.inkBlue,
      light: "#324456",
      dark: "#0f1823",
      contrastText: print.creamPanel,
    },
    error: {
      main: "#b6372b",
      light: "#d45d4f",
      dark: "#84261f",
      contrastText: "#fff7ea",
    },
    warning: {
      main: print.red,
      contrastText: "#fff7ea",
    },
    success: {
      main: print.green,
      contrastText: "#fff7ea",
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
    borderRadius: 0,
  },
  typography: {
    fontFamily:
      '"Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, "Times New Roman", serif',
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
  components: {
    MuiAlert: {
      styleOverrides: {
        root: {
          backgroundColor: "#fff4dd",
          border: `2px solid ${print.line}`,
          borderRadius: 0,
          boxShadow: `5px 5px 0 ${print.inkBlue}`,
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
          borderRadius: 0,
          boxShadow: `5px 5px 0 ${print.inkBlue}`,
          fontFamily: "inherit",
          fontWeight: 900,
          lineHeight: 1.15,
          minWidth: 0,
          position: "relative",
          transition:
            "background-color 140ms ease, border-color 140ms ease, color 140ms ease, transform 120ms ease, box-shadow 120ms ease",
          whiteSpace: "nowrap",
          "&:hover": {
            boxShadow: `6px 6px 0 ${print.inkBlue}`,
            transform: "translate(-1px, -1px)",
          },
          "&:active": {
            boxShadow: `2px 2px 0 ${print.inkBlue}`,
            transform: "translate(2px, 2px)",
          },
          "&.Mui-disabled": {
            boxShadow: "none",
            color: "rgba(25, 31, 35, 0.36)",
          },
        },
        contained: {
          backgroundColor: print.red,
          border: `2px solid ${print.line}`,
          color: "#fff7ea",
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
          backgroundColor: "#efe1c5",
          border: `2px solid ${print.line}`,
          borderRadius: 0,
          color: print.ink,
          fontFamily: "inherit",
          fontWeight: 900,
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          backgroundColor: "#fff4dd",
          border: `2px solid ${print.line}`,
          borderRadius: 0,
          boxShadow: `4px 4px 0 ${print.inkBlue}`,
          color: print.ink,
          transition: "background-color 140ms ease, color 140ms ease, transform 120ms ease, box-shadow 120ms ease",
          "&:hover": {
            backgroundColor: "#f1ddbd",
            boxShadow: `5px 5px 0 ${print.inkBlue}`,
            transform: "translate(-1px, -1px)",
          },
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          backgroundColor: "#ddc7a3",
          border: `2px solid ${print.line}`,
          borderRadius: 0,
        },
        bar: {
          backgroundColor: print.red,
        },
      },
    },
    MuiMenu: {
      styleOverrides: {
        paper: {
          backgroundImage: "none",
          backgroundColor: print.creamPanel,
          border: `2px solid ${print.line}`,
          borderRadius: 0,
          boxShadow: `6px 6px 0 ${print.inkBlue}`,
          color: print.ink,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundColor: print.creamPanel,
          backgroundImage: "none",
          borderRadius: 0,
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
          border: `2px solid ${print.line}`,
          borderRadius: "50%",
          boxShadow: `3px 3px 0 ${print.inkBlue}`,
          height: 18,
          width: 18,
          "&:hover, &.Mui-focusVisible": {
            boxShadow: `4px 4px 0 ${print.inkBlue}`,
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
            backgroundColor: "#fff8e9",
            border: `2px solid ${print.line}`,
            borderRadius: 0,
            color: print.ink,
            fontFamily: "inherit",
            "&:before, &:after": {
              display: "none",
            },
            "&:hover": {
              backgroundColor: "#fff4dd",
              borderColor: print.line,
            },
            "&.Mui-focused": {
              borderColor: print.red,
              boxShadow: `4px 4px 0 ${print.inkBlue}`,
            },
            "&.Mui-disabled": {
              backgroundColor: "#ead9bb",
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
          border: `2px solid ${print.line}`,
          borderRadius: 0,
          color: print.creamPanel,
          fontFamily: "inherit",
        },
      },
    },
  },
});
