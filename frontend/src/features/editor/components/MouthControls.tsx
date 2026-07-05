import { Box, Button, Group, Stack } from "../../../ui/mui";
import { useState } from "react";
import type { EditRecipe, MouthControlKey } from "../deformation/recipe";
import { mouthControlRanges } from "../deformation/recipe";
import { ParameterSlider } from "./ParameterSlider";

type MouthControlConfig = {
  actualMax: number;
  actualMin: number;
  key: MouthControlKey;
  label: string;
};

const mouthControls: MouthControlConfig[] = [
  { actualMax: 0.05, actualMin: -0.05, key: "mouthHorizontal", label: "左右位置" },
  { actualMax: 0.06, actualMin: -0.06, key: "mouthVertical", label: "上下位置" },
  { actualMax: 0.45, actualMin: -0.45, key: "mouthWidth", label: "嘴巴宽度" },
  { actualMax: 0.35, actualMin: -0.35, key: "mouthSize", label: "嘴巴大小" },
  { actualMax: 0.08, actualMin: -0.08, key: "mouthSmile", label: "微笑弧度" },
];

export type MouthControlsProps = {
  compact?: boolean;
  debugValues?: boolean;
  values: EditRecipe["mouth"];
  onChange: (key: MouthControlKey, value: number) => void;
  onReset: (key: MouthControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
};

export function MouthControls({
  compact = false,
  debugValues = false,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
}: MouthControlsProps) {
  const [activeControlKey, setActiveControlKey] = useState<MouthControlKey>(mouthControls[0].key);
  const activeControl = mouthControls.find((control) => control.key === activeControlKey) ?? mouthControls[0];

  if (compact) {
    return (
      <Stack gap="sm" data-testid="mouth-controls-compact">
        <Box className="editor-horizontal-scroll" style={{ paddingBottom: 2 }}>
          <Group gap={0.75} wrap="nowrap">
            {mouthControls.map((control) => (
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
        <MouthControlSlider
          control={activeControl}
          debugValues={debugValues}
          onChange={onChange}
          onReset={onReset}
          onSliderInteractionEnd={onSliderInteractionEnd}
          onSliderInteractionStart={onSliderInteractionStart}
          values={values}
        />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      {mouthControls.map((control) => (
        <MouthControlSlider
          key={control.key}
          control={control}
          debugValues={debugValues}
          onChange={onChange}
          onReset={onReset}
          onSliderInteractionEnd={onSliderInteractionEnd}
          onSliderInteractionStart={onSliderInteractionStart}
          values={values}
        />
      ))}
    </Stack>
  );
}

function MouthControlSlider({
  control,
  debugValues,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
}: {
  control: MouthControlConfig;
  debugValues?: boolean;
  values: EditRecipe["mouth"];
  onChange: (key: MouthControlKey, value: number) => void;
  onReset: (key: MouthControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
}) {
  return (
    <ParameterSlider
      dataTestId={`mouth-control-${control.key}`}
      defaultValue={0}
      label={control.label}
      max={1}
      min={-1}
      precision={1}
      step={0.1}
      value={toDisplayValue(values[control.key], control)}
      debugValueFormatter={debugValues ? (value) => formatRealValue(toActualValue(value, control), control) : undefined}
      onChange={(value) => onChange(control.key, toActualValue(value, control))}
      onInteractionEnd={onSliderInteractionEnd}
      onInteractionStart={onSliderInteractionStart}
      onReset={() => onReset(control.key)}
    />
  );
}

function toDisplayValue(value: number, control: MouthControlConfig) {
  const scale = Math.max(Math.abs(control.actualMin), Math.abs(control.actualMax));
  return roundDisplay(scale > 0 ? value / scale : 0);
}

function toActualValue(value: number, control: MouthControlConfig) {
  const range = mouthControlRanges[control.key];
  const scale = Math.max(Math.abs(control.actualMin), Math.abs(control.actualMax));
  return Number((value * scale).toFixed(range.precision));
}

function roundDisplay(value: number) {
  return Number(value.toFixed(1));
}

function formatRealValue(value: number, control: MouthControlConfig) {
  return value.toFixed(mouthControlRanges[control.key].precision);
}
