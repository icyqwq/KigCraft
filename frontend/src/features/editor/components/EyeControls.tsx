import { Box, Button, Group, Stack, Switch } from "../../../ui/mui";
import { useState } from "react";
import type { EditRecipe, EyeControlKey } from "../deformation/recipe";
import { defaultEyeControlValues, eyeControlRanges } from "../deformation/recipe";
import { ParameterSlider } from "./ParameterSlider";

type EyeControlConfig = {
  actualMax: number;
  actualMin: number;
  key: EyeControlKey;
  label: string;
  max?: number;
  min?: number;
  offset?: number;
};

const eyeControls: EyeControlConfig[] = [
  { actualMax: 0.3, actualMin: -0.3, key: "eyeSize", label: "眼睛大小" },
  { actualMax: 0.3, actualMin: -0.3, key: "eyeHeight", label: "眼高" },
  { actualMax: 0.5, actualMin: -0.5, key: "eyeWidth", label: "眼宽" },
  { actualMax: 0.05, actualMin: -0.05, key: "eyeDistance", label: "眼距" },
  { actualMax: 0.05, actualMin: -0.05, key: "eyeVertical", label: "眼睛上下位置" },
  { actualMax: 0.5, actualMin: -0.5, key: "eyeTilt", label: "眼角倾斜" },
  {
    actualMax: 70,
    actualMin: -30,
    key: "eyeRegionScale",
    label: "眼睛选区范围",
    offset: defaultEyeControlValues.eyeRegionScale,
  },
];

export type EyeControlsProps = {
  compact?: boolean;
  debugValues?: boolean;
  values: EditRecipe["eyes"];
  onChange: (key: EyeControlKey, value: number) => void;
  onReset: (key: EyeControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: (key: EyeControlKey) => void;
};

export function EyeControls({
  compact = false,
  debugValues = false,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
}: EyeControlsProps) {
  const [activeControlKey, setActiveControlKey] = useState<EyeControlKey>(eyeControls[0].key);
  const [expandedRangeEnabled, setExpandedRangeEnabled] = useState(false);
  const activeControl = eyeControls.find((control) => control.key === activeControlKey) ?? eyeControls[0];

  if (compact) {
    return (
      <Stack gap="sm" data-testid="eye-controls-compact">
        <Box className="editor-horizontal-scroll" style={{ paddingBottom: 2 }}>
          <Group gap={0.75} wrap="nowrap">
            {eyeControls.map((control) => (
              <Button
                key={control.key}
                color="gray"
                onClick={() => setActiveControlKey(control.key)}
                size="xs"
                variant="light"
                style={{
                  backgroundColor: control.key === activeControl.key ? "var(--kb-dirty-yellow)" : "var(--kb-panel)",
                  borderColor: "var(--kb-line)",
                  color: control.key === activeControl.key ? "var(--kb-off-white)" : "var(--kb-ink)",
                  flex: "0 0 auto",
                }}
              >
                {control.label}
              </Button>
            ))}
          </Group>
        </Box>
        <EyeControlSlider
          control={activeControl}
          debugValues={debugValues}
          values={values}
          onChange={onChange}
          onReset={onReset}
          onSliderInteractionEnd={onSliderInteractionEnd}
          onSliderInteractionStart={onSliderInteractionStart}
          expandedRangeEnabled={expandedRangeEnabled}
        />
        <Switch
          checked={expandedRangeEnabled}
          label="扩大参数范围"
          onChange={(event) => setExpandedRangeEnabled(event.currentTarget.checked)}
        />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      {eyeControls.map((control) => (
        <EyeControlSlider
          key={control.key}
          control={control}
          debugValues={debugValues}
          values={values}
          onChange={onChange}
          onReset={onReset}
          onSliderInteractionEnd={onSliderInteractionEnd}
          onSliderInteractionStart={onSliderInteractionStart}
          expandedRangeEnabled={expandedRangeEnabled}
        />
      ))}
      <Switch
        checked={expandedRangeEnabled}
        label="扩大参数范围"
        onChange={(event) => setExpandedRangeEnabled(event.currentTarget.checked)}
      />
    </Stack>
  );
}

function EyeControlSlider({
  control,
  debugValues,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
  expandedRangeEnabled,
}: {
  control: EyeControlConfig;
  debugValues?: boolean;
  expandedRangeEnabled: boolean;
  values: EditRecipe["eyes"];
  onChange: (key: EyeControlKey, value: number) => void;
  onReset: (key: EyeControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: (key: EyeControlKey) => void;
}) {
  return (
    <ParameterSlider
      dataTestId={`eye-control-${control.key}`}
      defaultValue={0}
      label={control.label}
      max={control.max ?? 1}
      min={control.min ?? -1}
      precision={2}
      step={0.01}
      value={toDisplayValue(values[control.key], control, expandedRangeEnabled)}
      debugValueFormatter={debugValues ? (value) => formatRealValue(toActualValue(value, control, expandedRangeEnabled), control) : undefined}
      onChange={(value) => onChange(control.key, toActualValue(value, control, expandedRangeEnabled))}
      onInteractionEnd={onSliderInteractionEnd}
      onInteractionStart={() => onSliderInteractionStart?.(control.key)}
      onReset={() => onReset(control.key)}
    />
  );
}

function toDisplayValue(value: number, control: EyeControlConfig, expandedRangeEnabled: boolean) {
  return toNormalizedAlgorithmValue(value, getEffectiveControl(control, expandedRangeEnabled));
}

function toNormalizedAlgorithmValue(value: number, control: EyeControlConfig) {
  const offset = control.offset ?? 0;
  const scale = Math.max(Math.abs(control.actualMin - offset), Math.abs(control.actualMax - offset));
  return roundDisplay((value - offset) / scale);
}

function toActualValue(value: number, control: EyeControlConfig, expandedRangeEnabled: boolean) {
  const effectiveControl = getEffectiveControl(control, expandedRangeEnabled);
  const offset = control.offset ?? 0;
  const range = eyeControlRanges[control.key];
  const scale = Math.max(Math.abs(effectiveControl.actualMin - offset), Math.abs(effectiveControl.actualMax - offset));
  return roundActual(offset + value * scale, range.precision);
}

function getEffectiveControl(control: EyeControlConfig, expandedRangeEnabled: boolean): EyeControlConfig {
  if (!expandedRangeEnabled) return control;
  const offset = control.offset ?? 0;
  return {
    ...control,
    actualMax: offset + (control.actualMax - offset) * 2,
    actualMin: offset + (control.actualMin - offset) * 2,
  };
}

function roundDisplay(value: number) {
  return Number(value.toFixed(2));
}

function roundActual(value: number, precision = 4) {
  return Number(value.toFixed(precision));
}

function formatRealValue(value: number, control: EyeControlConfig) {
  return value.toFixed(eyeControlRanges[control.key].precision);
}
