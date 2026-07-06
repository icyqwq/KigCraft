import { ActionIcon, Group, SegmentedControl, Slider, Stack, Switch, Text, Tooltip } from "../../../ui/mui";
import { IconArrowBackUp, IconArrowForwardUp, IconRefresh } from "@tabler/icons-react";

export type LiquifyToolMode = "warp" | "scale";

const liquifyToolModes: Array<{ label: string; value: LiquifyToolMode }> = [
  { label: "变形笔刷", value: "warp" },
  { label: "局部缩放", value: "scale" },
];

export type LiquifyControlsProps = {
  brushRadius: number;
  canRedo: boolean;
  canUndo: boolean;
  compact?: boolean;
  scaleAmount: number;
  symmetryAxis: number;
  symmetryEnabled: boolean;
  toolMode: LiquifyToolMode;
  warpStrength: number;
  onBrushRadiusChange: (value: number) => void;
  onBrushRadiusReset: () => void;
  onRedo: () => void;
  onScaleChange: (value: number) => void;
  onScaleReset: () => void;
  onSymmetryAxisChange: (value: number) => void;
  onSymmetryAxisReset: () => void;
  onSymmetryEnabledChange: (enabled: boolean) => void;
  onToolModeChange: (mode: LiquifyToolMode) => void;
  onUndo: () => void;
  onWarpStrengthChange: (value: number) => void;
  onWarpStrengthReset: () => void;
};

export function LiquifyControls({
  brushRadius,
  canRedo,
  canUndo,
  compact = false,
  scaleAmount,
  symmetryAxis,
  symmetryEnabled,
  toolMode,
  warpStrength,
  onBrushRadiusChange,
  onBrushRadiusReset,
  onRedo,
  onScaleChange,
  onScaleReset,
  onSymmetryAxisChange,
  onSymmetryAxisReset,
  onSymmetryEnabledChange,
  onToolModeChange,
  onUndo,
  onWarpStrengthChange,
  onWarpStrengthReset,
}: LiquifyControlsProps) {
  return (
    <Stack gap={compact ? "sm" : "md"} data-testid={compact ? "liquify-controls-compact" : undefined}>
      <SegmentedControl
        color="cyan"
        data={liquifyToolModes}
        data-testid="liquify-tool-mode"
        fullWidth={compact}
        onChange={(value) => onToolModeChange(value as LiquifyToolMode)}
        size="xs"
        value={toolMode}
      />

      <Stack gap={0.75}>
        <Group justify="space-between" wrap="nowrap">
          <Text c="white" fw={600} size="sm">
            笔刷大小
          </Text>
          <Text c="dimmed" size="sm">
            {brushRadius}px
          </Text>
        </Group>
        <Group align="center" gap="xs" wrap="nowrap">
          <Slider
            color="cyan"
            data-testid="liquify-radius-slider"
            max={160}
            min={12}
            onChange={onBrushRadiusChange}
            size="sm"
            step={1}
            style={{ flex: 1 }}
            thumbLabel="笔刷大小"
            value={brushRadius}
          />
          <Tooltip label="重置笔刷大小">
            <ActionIcon
              aria-label="重置笔刷大小"
              data-testid="liquify-radius-reset"
              onClick={onBrushRadiusReset}
              size="sm"
              variant="subtle"
            >
              <IconRefresh size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Stack>

      {toolMode === "warp" ? (
        <Stack gap={0.75}>
          <Group justify="space-between" wrap="nowrap">
            <Text c="white" fw={600} size="sm">
              变形强度
            </Text>
            <Text c="dimmed" size="sm">
              {warpStrength.toFixed(2)}
            </Text>
          </Group>
          <Group align="center" gap="xs" wrap="nowrap">
            <Slider
              color="cyan"
              data-testid="liquify-warp-strength-slider"
              max={0.5}
              min={0}
              onChange={onWarpStrengthChange}
              size="sm"
              step={0.01}
              style={{ flex: 1 }}
              thumbLabel="变形强度"
              value={warpStrength}
            />
            <Tooltip label="重置变形强度">
              <ActionIcon
                aria-label="重置变形强度"
                data-testid="liquify-warp-strength-reset"
                onClick={onWarpStrengthReset}
                size="sm"
                variant="subtle"
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
          <Group justify="space-between" wrap="nowrap">
            <Text c="white" fw={600} size="sm">
              对称
            </Text>
            <Switch
              aria-label="对称液化"
              checked={symmetryEnabled}
              data-testid="liquify-symmetry-toggle"
              onChange={(event) => onSymmetryEnabledChange(event.currentTarget.checked)}
            />
          </Group>
          {symmetryEnabled ? (
            <Stack gap={0.75}>
              <Group justify="space-between" wrap="nowrap">
                <Text c="white" fw={600} size="sm">
                  脸中线
                </Text>
                <Text c="dimmed" size="sm">
                  {(symmetryAxis * 100).toFixed(1)}%
                </Text>
              </Group>
              <Group align="center" gap="xs" wrap="nowrap">
                <Slider
                  color="cyan"
                  data-testid="liquify-symmetry-axis-slider"
                  max={0.65}
                  min={0.35}
                  onChange={onSymmetryAxisChange}
                  size="sm"
                  step={0.001}
                  style={{ flex: 1 }}
                  thumbLabel="脸中线"
                  value={symmetryAxis}
                />
                <Tooltip label="重置脸中线">
                  <ActionIcon
                    aria-label="重置脸中线"
                    data-testid="liquify-symmetry-axis-reset"
                    onClick={onSymmetryAxisReset}
                    size="sm"
                    variant="subtle"
                  >
                    <IconRefresh size={14} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Stack>
          ) : null}
        </Stack>
      ) : null}

      {toolMode === "scale" ? (
        <Stack gap={0.75}>
          <Group justify="space-between" wrap="nowrap">
            <Text c="white" fw={600} size="sm">
              缩放强度
            </Text>
            <Text c="dimmed" size="sm">
              {scaleAmount.toFixed(1)}
            </Text>
          </Group>
          <Group align="center" gap="xs" wrap="nowrap">
            <Slider
              color="cyan"
              data-testid="liquify-scale-slider"
              max={10}
              min={-10}
              onChange={onScaleChange}
              size="sm"
              step={0.1}
              style={{ flex: 1 }}
              thumbLabel="缩放强度"
              value={scaleAmount}
            />
            <Tooltip label="重置缩放强度">
              <ActionIcon
                aria-label="重置缩放强度"
                data-testid="liquify-scale-reset"
                onClick={onScaleReset}
                size="sm"
                variant="subtle"
              >
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Stack>
      ) : null}

      <Group gap="xs" wrap="nowrap">
        <Tooltip label="撤销 Ctrl+Z">
          <ActionIcon
            aria-label="撤销"
            color={canUndo ? "cyan" : "gray"}
            data-active={canUndo ? "true" : "false"}
            data-testid="liquify-undo"
            disabled={!canUndo}
            onClick={onUndo}
            size="lg"
            variant={canUndo ? "light" : "subtle"}
          >
            <IconArrowBackUp size={18} />
          </ActionIcon>
        </Tooltip>
        <Tooltip label="重做 Ctrl+Y / Ctrl+Shift+Z">
          <ActionIcon
            aria-label="重做"
            color={canRedo ? "cyan" : "gray"}
            data-active={canRedo ? "true" : "false"}
            data-testid="liquify-redo"
            disabled={!canRedo}
            onClick={onRedo}
            size="lg"
            variant={canRedo ? "light" : "subtle"}
          >
            <IconArrowForwardUp size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>
    </Stack>
  );
}
