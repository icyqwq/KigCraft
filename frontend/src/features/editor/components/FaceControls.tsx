import { Box, Button, Group, Stack, Switch } from "../../../ui/mui";
import { useState } from "react";
import type { EditRecipe, FaceControlKey } from "../deformation/recipe";
import { faceControlRanges } from "../deformation/recipe";
import { ParameterSlider } from "./ParameterSlider";

type FaceControlConfig = {
  actualMax: number;
  actualMin: number;
  key: FaceControlKey;
  label: string;
  max?: number;
  min?: number;
};

const faceControls: FaceControlConfig[] = [
  { actualMax: 0.2, actualMin: -0.2, key: "faceWidth", label: "脸宽" },
  { actualMax: 0.3, actualMin: -0.3, key: "faceLength", label: "脸长" },
  { actualMax: 0.2, actualMin: -0.2, key: "midFaceLength", label: "中庭长度" },
  { actualMax: 0.2, actualMin: 0, key: "smallFace", label: "小脸", max: 1, min: 0 },
  { actualMax: 0.2, actualMin: -0.2, key: "cheekbone", label: "颧骨" },
  { actualMax: 0.2, actualMin: -0.2, key: "chinLength", label: "下巴长度" },
  { actualMax: 0.2, actualMin: -0.2, key: "chinPoint", label: "下巴尖度" },
  { actualMax: 0.2, actualMin: 0, key: "vLine", label: "V脸", max: 1, min: 0 },
  { actualMax: 0.2, actualMin: -0.2, key: "jawAngle", label: "下颌角" },
];

export type FaceControlsProps = {
  compact?: boolean;
  debugValues?: boolean;
  values: EditRecipe["face"];
  onChange: (key: FaceControlKey, value: number) => void;
  onReset: (key: FaceControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
};

export function FaceControls({
  compact = false,
  debugValues = false,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
}: FaceControlsProps) {
  const [activeControlKey, setActiveControlKey] = useState<FaceControlKey>(faceControls[0].key);
  const [expandedRangeEnabled, setExpandedRangeEnabled] = useState(false);
  const activeControl = faceControls.find((control) => control.key === activeControlKey) ?? faceControls[0];

  if (compact) {
    return (
      <Stack gap="sm" data-testid="face-controls-compact">
        <Box className="editor-horizontal-scroll" style={{ paddingBottom: 2 }}>
          <Group gap={0.75} wrap="nowrap">
            {faceControls.map((control) => (
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
        <FaceControlSlider
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
      {faceControls.map((control) => (
        <FaceControlSlider
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

function FaceControlSlider({
  control,
  debugValues,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
  expandedRangeEnabled,
}: {
  control: FaceControlConfig;
  debugValues?: boolean;
  expandedRangeEnabled: boolean;
  values: EditRecipe["face"];
  onChange: (key: FaceControlKey, value: number) => void;
  onReset: (key: FaceControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
}) {
  return (
    <ParameterSlider
      dataTestId={`face-control-${control.key}`}
      label={control.label}
      max={control.max ?? 1}
      min={control.min ?? -1}
      precision={2}
      step={0.01}
      value={toDisplayValue(values[control.key], control, expandedRangeEnabled)}
      debugValueFormatter={debugValues ? (value) => formatRealValue(toActualValue(value, control, expandedRangeEnabled), control) : undefined}
      onChange={(value) => onChange(control.key, toActualValue(value, control, expandedRangeEnabled))}
      onInteractionEnd={onSliderInteractionEnd}
      onInteractionStart={onSliderInteractionStart}
      onReset={() => onReset(control.key)}
    />
  );
}

function toDisplayValue(value: number, control: FaceControlConfig, expandedRangeEnabled: boolean) {
  return toAlgorithmValue(value, getEffectiveControl(control, expandedRangeEnabled));
}

function toAlgorithmValue(value: number, control: FaceControlConfig) {
  if (control.actualMin >= 0) {
    const span = control.actualMax - control.actualMin;
    return roundDisplay(span > 0 ? (value - control.actualMin) / span : 0);
  }
  const scale = Math.max(Math.abs(control.actualMin), Math.abs(control.actualMax));
  return roundDisplay(value / scale);
}

function toActualValue(value: number, control: FaceControlConfig, expandedRangeEnabled: boolean) {
  const effectiveControl = getEffectiveControl(control, expandedRangeEnabled);
  const range = faceControlRanges[control.key];
  if (effectiveControl.actualMin >= 0) {
    const span = effectiveControl.actualMax - effectiveControl.actualMin;
    return roundActual(effectiveControl.actualMin + value * span, range.precision);
  }
  const scale = Math.max(Math.abs(effectiveControl.actualMin), Math.abs(effectiveControl.actualMax));
  return roundActual(value * scale, range.precision);
}

function getEffectiveControl(control: FaceControlConfig, expandedRangeEnabled: boolean): FaceControlConfig {
  if (!expandedRangeEnabled) return control;
  return {
    ...control,
    actualMax: control.actualMax * 2,
    actualMin: control.actualMin * 2,
  };
}

function roundDisplay(value: number) {
  return Number(value.toFixed(2));
}

function roundActual(value: number, precision = 3) {
  return Number(value.toFixed(precision));
}

function formatRealValue(value: number, control: FaceControlConfig) {
  return value.toFixed(faceControlRanges[control.key].precision);
}
