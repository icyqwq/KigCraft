import { Box, Button, Group, Stack, Switch } from "../../../ui/mui";
import { useState } from "react";
import type { BrowControlKey, EditRecipe } from "../deformation/recipe";
import { browControlRanges } from "../deformation/recipe";
import { ParameterSlider } from "./ParameterSlider";

type BrowControlConfig = {
  /** Stored value at the slider end; the expanded range doubles it. */
  actualLimit: number;
  key: BrowControlKey;
  label: string;
};

const browControls: BrowControlConfig[] = [
  { actualLimit: 0.5, key: "browVertical", label: "上下" },
  { actualLimit: 0.5, key: "browThickness", label: "粗细" },
  { actualLimit: 0.5, key: "browLength", label: "长短" },
  { actualLimit: 0.5, key: "browSpacing", label: "间距" },
  { actualLimit: 0.5, key: "browHeadSpacing", label: "眉头间距" },
  { actualLimit: 0.5, key: "browTilt", label: "倾斜" },
  { actualLimit: 0.5, key: "browPeak", label: "眉峰" },
];

export type BrowControlsProps = {
  compact?: boolean;
  debugValues?: boolean;
  values: EditRecipe["brows"];
  onChange: (key: BrowControlKey, value: number) => void;
  onReset: (key: BrowControlKey) => void;
  onSliderInteractionEnd?: () => void;
  onSliderInteractionStart?: () => void;
};

export function BrowControls({
  compact = false,
  debugValues = false,
  values,
  onChange,
  onReset,
  onSliderInteractionEnd,
  onSliderInteractionStart,
}: BrowControlsProps) {
  const [activeControlKey, setActiveControlKey] = useState<BrowControlKey>(browControls[0].key);
  const [expandedRangeEnabled, setExpandedRangeEnabled] = useState(false);
  const activeControl = browControls.find((control) => control.key === activeControlKey) ?? browControls[0];
  const renderSlider = (control: BrowControlConfig) => {
    const limit = control.actualLimit * (expandedRangeEnabled ? 2 : 1);
    const precision = browControlRanges[control.key].precision;

    return (
      <ParameterSlider
        key={control.key}
        dataTestId={`brow-control-${control.key}`}
        defaultValue={0}
        label={control.label}
        max={1}
        min={-1}
        precision={2}
        step={0.01}
        value={Number((values[control.key] / limit).toFixed(2))}
        debugValueFormatter={debugValues ? (value) => (value * limit).toFixed(precision) : undefined}
        onChange={(value) => onChange(control.key, Number((value * limit).toFixed(precision)))}
        onInteractionEnd={onSliderInteractionEnd}
        onInteractionStart={onSliderInteractionStart}
        onReset={() => onReset(control.key)}
      />
    );
  };
  const rangeSwitch = (
    <Switch
      checked={expandedRangeEnabled}
      label="扩大参数范围"
      onChange={(event) => setExpandedRangeEnabled(event.currentTarget.checked)}
    />
  );

  if (compact) {
    return (
      <Stack gap="sm" data-testid="brow-controls-compact">
        <Box className="editor-horizontal-scroll" style={{ paddingBottom: 2 }}>
          <Group gap={0.75} wrap="nowrap">
            {browControls.map((control) => (
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
        {renderSlider(activeControl)}
        {rangeSwitch}
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      {browControls.map(renderSlider)}
      {rangeSwitch}
    </Stack>
  );
}
