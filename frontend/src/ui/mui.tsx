import MuiAlert from "@mui/material/Alert";
import MuiBox from "@mui/material/Box";
import MuiButton from "@mui/material/Button";
import MuiCard from "@mui/material/Card";
import MuiChip from "@mui/material/Chip";
import MuiContainer from "@mui/material/Container";
import MuiCircularProgress from "@mui/material/CircularProgress";
import MuiDivider from "@mui/material/Divider";
import MuiIconButton from "@mui/material/IconButton";
import MuiMenu from "@mui/material/Menu";
import MuiMenuItem from "@mui/material/MenuItem";
import MuiPaper from "@mui/material/Paper";
import MuiLinearProgress from "@mui/material/LinearProgress";
import MuiSkeleton from "@mui/material/Skeleton";
import MuiSlider from "@mui/material/Slider";
import MuiStack from "@mui/material/Stack";
import MuiSwitch from "@mui/material/Switch";
import MuiTextField from "@mui/material/TextField";
import MuiTooltip from "@mui/material/Tooltip";
import MuiTypography from "@mui/material/Typography";
import MuiTabs from "@mui/material/Tabs";
import MuiTab from "@mui/material/Tab";
import useMuiMediaQuery from "@mui/material/useMediaQuery";
import type { CSSProperties, ElementType, ReactElement, ReactNode, SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { translateLiteralNode, translateLiteralString } from "../i18n/literalTranslations";

type Responsive<T> = T | { base?: T; sm?: T; md?: T; lg?: T; xl?: T };

type SpacingValue = number | string;

type CommonStyleProps = {
  bg?: string;
  c?: string;
  color?: string;
  fw?: number | string;
  h?: number | string;
  maw?: number | string;
  mb?: SpacingValue;
  mih?: number | string;
  miw?: number | string;
  ml?: SpacingValue | "auto";
  mr?: SpacingValue | "auto";
  mt?: SpacingValue;
  mx?: SpacingValue;
  my?: SpacingValue;
  p?: Responsive<SpacingValue>;
  pb?: SpacingValue;
  pl?: SpacingValue;
  pr?: SpacingValue;
  pt?: SpacingValue;
  px?: Responsive<SpacingValue>;
  py?: Responsive<SpacingValue>;
  radius?: number | string;
  style?: CSSProperties;
  ta?: CSSProperties["textAlign"];
  tt?: CSSProperties["textTransform"];
  w?: number | string;
};

function mapColor(color?: string) {
  if (!color) return undefined;
  if (color === "dimmed") return "text.secondary";
  if (color === "white") return "text.primary";
  if (color === "cyan") return "primary.main";
  if (color === "cyan.2" || color === "cyan.3" || color === "cyan.4" || color === "cyan.5") return "primary.light";
  if (color === "gray") return "grey.500";
  if (color === "red.6" || color === "red") return "error.main";
  if (color === "green") return "success.main";
  if (color === "yellow") return "warning.main";
  if (color === "teal") return "success.main";
  return color;
}

function mapSpacing(value: SpacingValue | undefined) {
  if (value === undefined) return undefined;
  if (typeof value === "number") return value;
  const mapped: Record<string, number> = {
    xs: 0.5,
    sm: 1,
    md: 2,
    lg: 3,
    xl: 4,
  };
  return mapped[value] ?? value;
}

function mapRadius(value: number | string | undefined) {
  if (value === undefined) return undefined;
  if (typeof value === "number") return `${value}px`;
  const mapped: Record<string, string> = {
    xs: "6px",
    sm: "8px",
    md: "12px",
    lg: "16px",
    xl: "24px",
  };
  return mapped[value] ?? value;
}

const pxStyleKeys = new Set<string>([
  "borderBottomLeftRadius",
  "borderBottomRightRadius",
  "borderRadius",
  "borderTopLeftRadius",
  "borderTopRightRadius",
  "columnGap",
  "gap",
  "margin",
  "marginBlock",
  "marginBlockEnd",
  "marginBlockStart",
  "marginBottom",
  "marginInline",
  "marginInlineEnd",
  "marginInlineStart",
  "marginLeft",
  "marginRight",
  "marginTop",
  "padding",
  "paddingBlock",
  "paddingBlockEnd",
  "paddingBlockStart",
  "paddingBottom",
  "paddingInline",
  "paddingInlineEnd",
  "paddingInlineStart",
  "paddingLeft",
  "paddingRight",
  "paddingTop",
  "rowGap",
]);

function normalizeStyleForSx(style: CSSProperties | undefined) {
  if (!style) return undefined;

  return Object.fromEntries(
    Object.entries(style).map(([key, value]) => [
      key,
      typeof value === "number" && pxStyleKeys.has(key) ? `${value}px` : value,
    ]),
  ) as CSSProperties;
}

function responsiveValue<T>(value: Responsive<T> | undefined) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const mapped: Record<string, T | undefined> = {};
  if ("base" in value) mapped.xs = value.base;
  if ("sm" in value) mapped.sm = value.sm;
  if ("md" in value) mapped.md = value.md;
  if ("lg" in value) mapped.lg = value.lg;
  if ("xl" in value) mapped.xl = value.xl;
  return mapped;
}

function responsiveSpacing(value: Responsive<SpacingValue> | undefined) {
  const resolved = responsiveValue(value);
  if (!resolved || typeof resolved !== "object" || Array.isArray(resolved)) {
    return mapSpacing(resolved as SpacingValue | undefined);
  }
  return Object.fromEntries(Object.entries(resolved).map(([breakpoint, spacing]) => [breakpoint, mapSpacing(spacing)]));
}

function sxFromProps(props: CommonStyleProps) {
  const normalizedStyle = normalizeStyleForSx(props.style);

  return {
    bgcolor: props.bg,
    borderRadius: mapRadius(props.radius),
    color: mapColor(props.c ?? props.color),
    fontWeight: props.fw,
    height: props.h,
    maxWidth: props.maw,
    mb: mapSpacing(props.mb),
    minHeight: props.mih,
    minWidth: props.miw,
    ml: props.ml === "auto" ? "auto" : mapSpacing(props.ml),
    mr: props.mr === "auto" ? "auto" : mapSpacing(props.mr),
    mt: mapSpacing(props.mt),
    mx: mapSpacing(props.mx),
    my: mapSpacing(props.my),
    p: responsiveSpacing(props.p),
    pb: mapSpacing(props.pb),
    pl: mapSpacing(props.pl),
    pr: mapSpacing(props.pr),
    pt: mapSpacing(props.pt),
    px: responsiveSpacing(props.px),
    py: responsiveSpacing(props.py),
    textAlign: props.ta,
    textTransform: props.tt,
    width: props.w,
    ...normalizedStyle,
  };
}

function stripCommonProps<T extends CommonStyleProps>(props: T) {
  const {
    bg,
    c,
    color,
    fw,
    h,
    maw,
    mb,
    mih,
    miw,
    ml,
    mr,
    mt,
    mx,
    my,
    p,
    pb,
    pl,
    pr,
    pt,
    px,
    py,
    radius,
    style,
    ta,
    tt,
    w,
    ...rest
  } = props;
  return rest;
}

function useLiteralLanguage() {
  const { i18n } = useTranslation();
  return i18n?.language ?? "zh-CN";
}

function translateCommonProps<T extends object>(props: T, language: string) {
  const translated = { ...(props as Record<string, unknown>) };
  for (const key of ["aria-label", "alt", "placeholder", "title"] as const) {
    const value = translated[key];
    if (typeof value === "string") translated[key] = translateLiteralString(value, language);
  }
  if ("children" in translated) {
    translated.children = translateLiteralNode(translated.children as ReactNode, language);
  }
  return translated as T;
}

export function Box({
  component,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  component?: ElementType | string;
}) {
  const Component = MuiBox as any;
  const language = useLiteralLanguage();
  return <Component component={component} sx={sxFromProps(props)} {...translateCommonProps(stripCommonProps(props), language)} />;
}

export function Stack({
  align,
  gap,
  justify,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  align?: CSSProperties["alignItems"];
  children?: ReactNode;
  gap?: SpacingValue;
  justify?: CSSProperties["justifyContent"];
}) {
  const Component = MuiStack as any;
  const language = useLiteralLanguage();
  return (
    <Component
      sx={{ ...sxFromProps(props), alignItems: align, gap: mapSpacing(gap), justifyContent: justify }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Group({
  align,
  gap,
  grow,
  justify,
  wrap,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  align?: CSSProperties["alignItems"];
  children?: ReactNode;
  gap?: SpacingValue;
  grow?: boolean;
  justify?: CSSProperties["justifyContent"];
  wrap?: CSSProperties["flexWrap"];
}) {
  const Component = MuiStack as any;
  const language = useLiteralLanguage();
  return (
    <Component
      direction="row"
      sx={{
        ...sxFromProps(props),
        "& > *": grow ? { flex: 1 } : undefined,
        alignItems: align,
        flexWrap: wrap,
        gap: mapSpacing(gap),
        justifyContent: justify,
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Text({
  component,
  fw,
  size,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  component?: ElementType | string;
  size?: "xs" | "sm" | "md" | "lg" | "xl" | string;
}) {
  const language = useLiteralLanguage();
  const fontSize =
    size === "xs" ? "0.75rem" : size === "sm" ? "0.875rem" : size === "lg" ? "1.125rem" : size === "xl" ? "1.25rem" : size;
  return (
    <MuiTypography
      component={(component as ElementType | undefined) ?? "p"}
      sx={{ ...sxFromProps(props), fontSize, fontWeight: fw }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Title({
  order = 2,
  size,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  order?: 1 | 2 | 3 | 4 | 5 | 6;
  size?: "h1" | "h2" | "h3" | "h4" | string;
}) {
  const language = useLiteralLanguage();
  const variant = size && ["h1", "h2", "h3", "h4", "h5", "h6"].includes(size) ? size : (`h${order}` as const);
  return (
    <MuiTypography
      component={`h${order}`}
      variant={variant as "h1" | "h2" | "h3" | "h4" | "h5" | "h6"}
      sx={sxFromProps(props)}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Button({
  color,
  component,
  fullWidth,
  justify,
  leftSection,
  loading,
  rightSection,
  size,
  variant,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  color?: string;
  component?: ElementType | string;
  disabled?: boolean;
  fullWidth?: boolean;
  justify?: string;
  leftSection?: ReactNode;
  loading?: boolean;
  rightSection?: ReactNode;
  size?: "xs" | "sm" | "md" | "lg" | string;
  variant?: "filled" | "light" | "subtle" | "default" | string;
}) {
  const language = useLiteralLanguage();
  const muiVariant =
    variant === "filled" ? "contained" : variant === "text" ? "text" : variant === "contained" ? "contained" : "text";
  const muiSize = size === "xs" || size === "sm" ? "small" : size === "lg" ? "large" : "medium";
  const Component = MuiButton as any;
  const isSoftVariant = variant === "light" || variant === "default";
  const isSubtleVariant = variant === "subtle";
  const propSx = sxFromProps(props);
  const explicitBackground = propSx.bgcolor ?? propSx.backgroundColor;
  const explicitHoverBackground =
    explicitBackground === "var(--kb-dirty-yellow)" || explicitBackground === "primary.main"
      ? "var(--kb-button-hover)"
      : explicitBackground;
  const loadingIcon = loading ? (
    <MuiCircularProgress
      size={muiSize === "small" ? 16 : 18}
      thickness={5}
      sx={{ color: variant === "filled" ? "var(--kb-line)" : "var(--kb-accent)" }}
    />
  ) : null;
  const buttonSx = {
    ...propSx,
    bgcolor: explicitBackground ?? (isSoftVariant || isSubtleVariant ? "var(--kb-panel)" : undefined),
    border: "2px solid var(--kb-line)",
    borderRadius: 0,
    boxShadow: variant === "filled" || isSoftVariant ? "var(--kb-hard-shadow-sm)" : undefined,
    color: propSx.color ?? (color === "gray" || isSoftVariant || isSubtleVariant ? "text.primary" : undefined),
    justifyContent: justify === "flex-start" ? "flex-start" : justify === "center" ? "center" : undefined,
    minHeight: propSx.minHeight ?? (muiSize === "small" ? 34 : muiSize === "large" ? 48 : 40),
    px: propSx.px ?? (muiSize === "small" ? 1.5 : 2),
    "&:hover": {
      bgcolor: explicitHoverBackground ?? (isSoftVariant || isSubtleVariant ? "var(--kb-panel-hover)" : undefined),
      borderColor: "var(--kb-line)",
      boxShadow: variant === "filled" || isSoftVariant ? "6px 6px 0 var(--kb-shadow)" : "var(--kb-hard-shadow-sm)",
      transform: "translate(-1px, -1px)",
    },
    "&:active": {
      boxShadow: "2px 2px 0 var(--kb-shadow)",
      transform: "translate(2px, 2px)",
    },
    "&.Mui-disabled": {
      boxShadow: "none",
      transform: "none",
    },
  };
  return (
    <Component
      color={color === "cyan" ? "primary" : color === "red" ? "error" : color === "gray" ? "inherit" : undefined}
      component={component}
      disabled={Boolean(props.disabled) || loading}
      fullWidth={fullWidth}
      startIcon={loadingIcon ?? leftSection}
      endIcon={rightSection}
      size={muiSize}
      variant={(muiVariant as "text" | "contained") ?? "contained"}
      sx={buttonSx}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function ActionIcon({
  children,
  color,
  disabled,
  size,
  variant,
  ...props
  }: CommonStyleProps & {
    [key: string]: unknown;
    children?: ReactNode;
    color?: string;
    disabled?: boolean;
    size?: "sm" | "md" | "lg" | "xl" | string;
    variant?: string;
  }) {
  const language = useLiteralLanguage();
  const muiSize = size === "sm" ? "small" : size === "lg" || size === "xl" ? "large" : "medium";
  const iconSize = size === "sm" ? 32 : size === "lg" ? 44 : size === "xl" ? 52 : 38;
  const glyphSize = size === "sm" ? 18 : size === "lg" ? 26 : size === "xl" ? 30 : 22;
  const isFilled = variant === "filled";
  const isLight = variant === "light";
  return (
    <MuiIconButton
      color={color === "cyan" ? "primary" : color === "red" ? "error" : "default"}
      disabled={disabled}
      size={muiSize}
      sx={{
        ...sxFromProps(props),
        height: iconSize,
        width: iconSize,
        border: "2px solid var(--kb-line)",
        borderRadius: 0,
        boxShadow: "var(--kb-hard-shadow-sm)",
        bgcolor: isFilled ? "primary.main" : isLight ? "var(--kb-panel)" : "var(--kb-panel)",
        color: isFilled ? "primary.contrastText" : undefined,
        transition:
          "background-color 140ms ease, border-color 140ms ease, box-shadow 140ms ease, color 140ms ease, opacity 120ms ease, transform 120ms ease",
        "& svg": {
          height: glyphSize,
          width: glyphSize,
        },
        "&:hover": {
          bgcolor: isFilled ? "primary.dark" : "var(--kb-panel-hover)",
          borderColor: "var(--kb-line)",
          boxShadow: "6px 6px 0 var(--kb-shadow)",
          transform: "translate(-1px, -1px)",
        },
        "&:active": {
          boxShadow: "2px 2px 0 var(--kb-shadow)",
          transform: "translate(2px, 2px)",
        },
        "&.Mui-disabled": {
          bgcolor: "var(--kb-old-paper-2)",
          borderColor: "rgba(25, 31, 35, 0.28)",
          boxShadow: "none",
          color: "rgba(25, 31, 35, 0.36)",
          transform: "none",
        },
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    >
      {children}
    </MuiIconButton>
  );
}

export function Paper({
  shadow,
  withBorder,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  shadow?: string;
  withBorder?: boolean;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiPaper
      elevation={shadow ? 2 : 0}
      sx={{
        backgroundColor: "background.paper",
        border: withBorder ? "3px solid var(--kb-line)" : undefined,
        ...sxFromProps(props),
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export const Card = Paper;

export function Badge({
  children,
  color,
  leftSection,
  variant,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  color?: string;
  leftSection?: ReactNode;
  size?: string;
  variant?: string;
}) {
  const language = useLiteralLanguage();
  const propSx = sxFromProps(props);
  return (
    <MuiChip
      color={color === "cyan" ? "primary" : color === "green" || color === "teal" ? "success" : color === "yellow" ? "warning" : "default"}
      icon={leftSection as ReactElement | undefined}
      label={translateLiteralNode(children, language)}
      size="small"
      variant="filled"
      sx={{
        ...propSx,
        borderRadius: 0,
        bgcolor: propSx.bgcolor ?? (variant === "light" ? "var(--kb-old-paper-2)" : undefined),
        color: "var(--kb-ink)",
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Divider(props: CommonStyleProps & { [key: string]: unknown; orientation?: "horizontal" | "vertical" }) {
  const language = useLiteralLanguage();
  return (
    <MuiDivider
      flexItem={props.orientation === "vertical"}
      sx={sxFromProps(props)}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Slider(props: CommonStyleProps & { [key: string]: unknown }) {
  const language = useLiteralLanguage();
  const { label: _label, onChange, onChangeCommitted, thumbLabel: _thumbLabel, ...rest } = stripCommonProps(props) as {
    [key: string]: unknown;
    label?: unknown;
    onChange?: (value: number) => void;
    onChangeCommitted?: (event: Event | SyntheticEvent, value: number | number[]) => void;
    thumbLabel?: unknown;
  };
  return (
    <MuiSlider
      sx={sxFromProps(props)}
      onChange={(_, value) => onChange?.(Array.isArray(value) ? value[0] : value)}
      onChangeCommitted={onChangeCommitted}
      {...translateCommonProps(rest, language)}
    />
  );
}

export function TextInput({
  label,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  label?: ReactNode;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiTextField
      fullWidth
      label={translateLiteralNode(label, language)}
      size="small"
      variant="filled"
      sx={{
        ...sxFromProps(props),
        "& .MuiFilledInput-input": {
          padding: "12px 14px",
        },
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Textarea(props: CommonStyleProps & { [key: string]: unknown; autosize?: boolean; minRows?: number }) {
  const language = useLiteralLanguage();
  return (
    <MuiTextField
      fullWidth
      multiline
      size="small"
      variant="filled"
      sx={{
        ...sxFromProps(props),
        "& .MuiFilledInput-root": {
          alignItems: "flex-start",
          padding: 0,
        },
        "& .MuiFilledInput-input": {
          lineHeight: 1.55,
          padding: "14px 14px",
        },
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function NumberInput({
  onChange,
  value,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  onChange?: (value: string | number) => void;
  value?: string | number;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiTextField
      fullWidth
      size="small"
      type="number"
      variant="filled"
      value={value ?? ""}
      onChange={(event) => onChange?.(event.currentTarget.value)}
      sx={sxFromProps(props)}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Switch({
  checked,
  label,
  onChange,
}: {
  checked?: boolean;
  label?: ReactNode;
  onChange?: (event: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  const language = useLiteralLanguage();
  const translatedLabel = translateLiteralNode(label, language);
  return (
    <Group align="center" gap={1}>
      <MuiSwitch
        checked={checked}
        onChange={onChange}
        slotProps={typeof translatedLabel === "string" ? { input: { "aria-label": translatedLabel } } : undefined}
      />
      {translatedLabel ? <Text size="sm">{translatedLabel}</Text> : null}
    </Group>
  );
}

export function SimpleGrid({
  cols,
  spacing,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  cols?: Responsive<number>;
  spacing?: SpacingValue;
}) {
  const language = useLiteralLanguage();
  const mappedCols =
    typeof cols === "number"
      ? `repeat(${cols}, minmax(0, 1fr))`
      : cols
        ? {
            xs: `repeat(${cols.base ?? 1}, minmax(0, 1fr))`,
            sm: `repeat(${cols.sm ?? cols.base ?? 1}, minmax(0, 1fr))`,
            md: `repeat(${cols.md ?? cols.sm ?? cols.base ?? 1}, minmax(0, 1fr))`,
            lg: `repeat(${cols.lg ?? cols.md ?? cols.sm ?? cols.base ?? 1}, minmax(0, 1fr))`,
          }
        : undefined;
  return (
    <MuiBox
      sx={{
        display: "grid",
        gap: mapSpacing(spacing),
        gridTemplateColumns: mappedCols,
        ...sxFromProps(props),
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function SegmentedControl<T extends string = string>({
  data,
  fullWidth,
  onChange,
  value,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  data: Array<{ label: string; value: T }>;
  fullWidth?: boolean;
  onChange: (value: T) => void;
  value: T;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiBox
      sx={{
        ...sxFromProps(props),
        display: "grid",
        gap: 0.5,
        gridAutoColumns: "minmax(0, 1fr)",
        gridAutoFlow: "column",
        width: fullWidth ? "100%" : "fit-content",
      }}
      {...stripCommonProps(props)}
    >
      {data.map((item) => {
        const selected = item.value === value;
        const label = translateLiteralString(item.label, language);
        return (
          <MuiButton
            key={item.value}
            onClick={() => onChange(item.value)}
            size="small"
            variant={selected ? "contained" : "text"}
            sx={{
              bgcolor: selected ? "primary.main" : "var(--kb-panel)",
              border: "2px solid var(--kb-line)",
              borderRadius: 0,
              boxShadow: selected ? "var(--kb-hard-shadow-sm)" : undefined,
              color: selected ? "primary.contrastText" : "text.primary",
              minWidth: 0,
              px: 1.25,
              "&:hover": {
                bgcolor: selected ? "primary.dark" : "var(--kb-old-paper-2)",
              },
            }}
          >
            {label}
          </MuiButton>
        );
      })}
    </MuiBox>
  );
}

export function Alert({
  color,
  variant,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  color?: string;
  variant?: string;
}) {
  const language = useLiteralLanguage();
  const propSx = sxFromProps(props);
  return (
    <MuiAlert
      severity={color === "red" ? "error" : color === "yellow" ? "warning" : color === "green" ? "success" : "info"}
      variant={variant === "filled" ? "filled" : "standard"}
      sx={{
        ...propSx,
        bgcolor: propSx.bgcolor ?? (variant === "light" ? "var(--kb-panel)" : undefined),
        border: variant === "light" ? "2px solid var(--kb-line)" : undefined,
        boxShadow: variant === "light" ? "var(--kb-hard-shadow-sm)" : undefined,
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Container({
  size,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl" | string;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiContainer
      maxWidth={size === "xl" ? "xl" : size === "lg" ? "lg" : "xl"}
      sx={sxFromProps(props)}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Tooltip({
  children,
  label,
}: {
  children: ReactElement;
  label: ReactNode;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiTooltip title={translateLiteralNode(label, language)}>
      <span style={{ display: "inline-flex" }}>{children}</span>
    </MuiTooltip>
  );
}

export function Image({
  alt,
  fit,
  h,
  radius,
  src,
  title,
  w,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  alt?: string;
  fit?: CSSProperties["objectFit"];
  h?: number | string;
  radius?: number | string;
  src?: string;
  title?: string;
  w?: number | string;
}) {
  const language = useLiteralLanguage();
  const imageSx = {
    ...sxFromProps(props),
    borderRadius: mapRadius(radius),
    height: h,
    objectFit: fit,
    width: w,
  };
  return (
    <MuiBox
      component="img"
      alt={alt ? translateLiteralString(alt, language) : alt}
      src={src}
      title={title ? translateLiteralString(title, language) : title}
      sx={imageSx}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function ThemeIcon({
  children,
  color,
  size,
  variant,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  children?: ReactNode;
  color?: string;
  size?: number | string;
  variant?: string;
}) {
  const language = useLiteralLanguage();
  const iconSx = {
    ...sxFromProps(props),
    alignItems: "center",
    bgcolor: variant === "light" ? "var(--kb-panel)" : mapColor(color),
    border: "2px solid var(--kb-line)",
    borderRadius: 0,
    boxShadow: "var(--kb-hard-shadow-sm)",
    color: variant === "light" ? mapColor(color) ?? "primary.main" : "primary.contrastText",
    display: "inline-flex",
    height: size,
    justifyContent: "center",
    minWidth: size,
    width: size,
  };
  return (
    <MuiBox
      sx={iconSx}
      {...translateCommonProps(stripCommonProps(props), language)}
    >
      {translateLiteralNode(children, language)}
    </MuiBox>
  );
}

export function AppShell({
  children,
}: {
  children: ReactNode;
  header?: { height: number };
  padding?: number;
}) {
  const language = useLiteralLanguage();
  return <MuiBox>{translateLiteralNode(children, language)}</MuiBox>;
}

AppShell.Header = function AppShellHeader(props: CommonStyleProps & { [key: string]: unknown; children?: ReactNode }) {
  const language = useLiteralLanguage();
  return (
    <MuiBox
      component="header"
      sx={{
        ...sxFromProps(props),
        minHeight: 72,
      }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
};

AppShell.Main = function AppShellMain(props: CommonStyleProps & { [key: string]: unknown; children?: ReactNode }) {
  const language = useLiteralLanguage();
  return <MuiBox component="main" sx={sxFromProps(props)} {...translateCommonProps(stripCommonProps(props), language)} />;
};

export const Menu = MuiMenu;
export const MenuItem = MuiMenuItem;
export const Tabs = MuiTabs;
export const Tab = MuiTab;
export function useMediaQuery(query: string, defaultValue?: boolean) {
  return useMuiMediaQuery(query, { defaultMatches: defaultValue });
}

export function Progress({
  value,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  value?: number;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiLinearProgress
      variant="determinate"
      value={value ?? 0}
      sx={{ ...sxFromProps(props), height: 8, borderRadius: 999 }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function CircularProgress({
  size,
  thickness,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  size?: number | string;
  thickness?: number;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiCircularProgress
      size={size}
      thickness={thickness}
      sx={sxFromProps(props)}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

export function Skeleton({
  height,
  radius,
  ...props
}: CommonStyleProps & {
  [key: string]: unknown;
  height?: number | string;
  radius?: number | string;
}) {
  const language = useLiteralLanguage();
  return (
    <MuiSkeleton
      height={height}
      sx={{ ...sxFromProps(props), borderRadius: mapRadius(radius) }}
      {...translateCommonProps(stripCommonProps(props), language)}
    />
  );
}

