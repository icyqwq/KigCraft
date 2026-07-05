import { ActionIcon, Button, Divider, Group, Slider, Text, Tooltip, useMediaQuery } from "../../../ui/mui";
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconBug,
  IconDeviceFloppy,
  IconEye,
  IconEyeOff,
  IconMaximize,
  IconRefresh,
  IconTrash,
  IconWand,
} from "@tabler/icons-react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";

export type EditorToolbarProps = {
  canRedo?: boolean;
  canUndo?: boolean;
  isComparingOriginal?: boolean;
  isRecognizingFace?: boolean;
  isRegenerating?: boolean;
  landmarkDebugMode?: boolean;
  landmarksVisible?: boolean;
  regenerateLabel?: string;
  secondaryRegenerateLabel?: string;
  showCompareAction?: boolean;
  showRegenerateActions?: boolean;
  showLandmarkToggle?: boolean;
  showPrimaryActions?: boolean;
  showViewportActions?: boolean;
  zoomPercent?: number;
  onClearImage?: () => void;
  onCompareEnd?: () => void;
  onCompareStart?: () => void;
  onFit?: () => void;
  onRedo?: () => void;
  onRegenerate?: () => void | Promise<void>;
  onRecognizeFace?: () => void | Promise<void>;
  onResetAll?: () => void;
  onSecondaryRegenerate?: () => void | Promise<void>;
  onSave?: () => void;
  onToggleLandmarkDebugMode?: () => void;
  onToggleLandmarks?: () => void;
  onUndo?: () => void;
  onZoomChange?: (value: number) => void;
};

export function EditorToolbar({
  canRedo = false,
  canUndo = false,
  isComparingOriginal = false,
  isRecognizingFace = false,
  isRegenerating = false,
  landmarkDebugMode = false,
  landmarksVisible = false,
  regenerateLabel = "重新生成正视图",
  secondaryRegenerateLabel,
  showCompareAction = true,
  showRegenerateActions = true,
  showLandmarkToggle = false,
  showPrimaryActions = true,
  showViewportActions = true,
  zoomPercent = 100,
  onClearImage,
  onCompareEnd,
  onCompareStart,
  onFit,
  onRedo,
  onRegenerate,
  onRecognizeFace,
  onResetAll,
  onSecondaryRegenerate,
  onSave,
  onToggleLandmarkDebugMode,
  onToggleLandmarks,
  onUndo,
  onZoomChange,
}: EditorToolbarProps) {
  const isCompactToolbar = useMediaQuery("(max-width: 720px)", false);
  const justify =
    showPrimaryActions && showViewportActions ? "space-between" : showViewportActions ? "flex-end" : "flex-end";
  const canCompare = Boolean(onCompareStart && onCompareEnd);
  const showHistoryActions = Boolean(onUndo || onRedo);
  const shouldShowRegenerateActions = showRegenerateActions && Boolean(onRegenerate || onSecondaryRegenerate);
  const showFaceRecognition = Boolean(onRecognizeFace);
  const isViewportOnlyToolbar = showViewportActions && !showPrimaryActions;
  const shouldWrapToolbar = isCompactToolbar && !isViewportOnlyToolbar;
  const regenerateButtonStyle = isCompactToolbar ? { flex: "1 1 0", minWidth: 0, whiteSpace: "nowrap" } : { whiteSpace: "nowrap" };

  return (
    <Group
      align="center"
      className={isViewportOnlyToolbar ? "editor-viewport-toolbar" : "editor-horizontal-scroll"}
      gap={isCompactToolbar ? 1 : "xs"}
      justify={justify}
      wrap={shouldWrapToolbar ? "wrap" : "nowrap"}
      style={{
        minHeight: 42,
        overflowX: isViewportOnlyToolbar ? "hidden" : shouldWrapToolbar ? "visible" : undefined,
        padding: isViewportOnlyToolbar ? "2px 7px 7px 2px" : undefined,
        rowGap: shouldWrapToolbar ? 8 : undefined,
        width: "100%",
      }}
    >
      {showPrimaryActions ? (
        <Group
          gap={1}
          justify="space-between"
          wrap={isCompactToolbar ? "wrap" : "nowrap"}
          style={{ flex: "1 1 auto", minWidth: 0, rowGap: isCompactToolbar ? 8 : undefined }}
        >
          {shouldShowRegenerateActions ? (
            <Group
              gap={1}
              justify="flex-start"
              wrap={isCompactToolbar ? "wrap" : "nowrap"}
              style={{
                flex: isCompactToolbar ? "1 1 100%" : "0 0 auto",
                minWidth: 0,
                order: isCompactToolbar ? 0 : 2,
                rowGap: isCompactToolbar ? 8 : undefined,
                width: isCompactToolbar ? "100%" : undefined,
              }}
            >
              {onRegenerate ? (
                <Button
                  color="cyan"
                  data-testid="editor-regenerate"
                  disabled={isRegenerating}
                  leftSection={<IconWand size={16} />}
                  loading={isRegenerating}
                  onClick={() => void onRegenerate()}
                  size="sm"
                  style={regenerateButtonStyle}
                  variant="filled"
                >
                  {regenerateLabel}
                </Button>
              ) : null}
              {onSecondaryRegenerate ? (
                <Button
                  color="cyan"
                  data-testid="editor-secondary-regenerate"
                  disabled={isRegenerating}
                  leftSection={<IconWand size={16} />}
                  loading={isRegenerating}
                  onClick={() => void onSecondaryRegenerate()}
                  size="sm"
                  style={regenerateButtonStyle}
                  variant="filled"
                >
                  {secondaryRegenerateLabel}
                </Button>
              ) : null}
            </Group>
          ) : null}

          <Group
            gap={1}
            wrap={isCompactToolbar ? "wrap" : "nowrap"}
            style={{ flex: isCompactToolbar ? "1 1 100%" : "0 0 auto", marginLeft: isCompactToolbar ? 0 : "auto", order: 1 }}
          >
            <Button
              color="gray"
              data-testid="editor-save"
              leftSection={<IconDeviceFloppy size={16} />}
              onClick={onSave}
              size="sm"
              style={{ whiteSpace: "nowrap" }}
              variant="light"
            >
              保存
            </Button>
            {onClearImage ? (
              <Button
                color="gray"
                data-testid="editor-clear-image"
                leftSection={<IconTrash size={16} />}
                onClick={onClearImage}
                size="sm"
                style={{ whiteSpace: "nowrap" }}
                variant="light"
              >
                清空图片
              </Button>
            ) : null}
            <Button
              color="gray"
              data-testid="editor-reset-all"
              leftSection={<IconRefresh size={16} />}
              onClick={onResetAll}
              size="sm"
              style={{ whiteSpace: "nowrap" }}
              variant="light"
            >
              重置全部
            </Button>
            {showFaceRecognition ? (
              <Button
                color="gray"
                data-testid="editor-recognize-face"
                disabled={isRecognizingFace}
                leftSection={<IconRefresh size={16} />}
                loading={isRecognizingFace}
                onClick={() => void onRecognizeFace?.()}
                size="sm"
                style={{ whiteSpace: "nowrap" }}
                variant="light"
              >
                重新识别关键点
              </Button>
            ) : null}
          </Group>
        </Group>
      ) : null}

      {showViewportActions ? (
        <Group
          align="center"
          data-testid="editor-canvas-toolbar"
          gap={isViewportOnlyToolbar && isCompactToolbar ? 0.35 : 0.75}
          justify={isCompactToolbar ? "flex-start" : showPrimaryActions ? undefined : "flex-end"}
          wrap={isViewportOnlyToolbar ? "nowrap" : shouldWrapToolbar ? "wrap" : "nowrap"}
          style={{
            flex: showPrimaryActions ? "0 0 auto" : "1 1 auto",
            minHeight: 42,
            minWidth: 0,
            overflowX: isViewportOnlyToolbar ? "hidden" : shouldWrapToolbar ? "visible" : "hidden",
            rowGap: shouldWrapToolbar ? 8 : undefined,
            width: "100%",
          }}
        >
          {showHistoryActions ? (
            <>
              <ToolbarIconButton disabled={!onUndo || !canUndo} label="撤销" onClick={onUndo}>
                <IconArrowBackUp size={20} />
              </ToolbarIconButton>
              <ToolbarIconButton disabled={!onRedo || !canRedo} label="重做" onClick={onRedo}>
                <IconArrowForwardUp size={20} />
              </ToolbarIconButton>

              <Divider color="var(--kb-line)" orientation="vertical" />
            </>
          ) : null}

          {showCompareAction ? (
            <Tooltip label="按住对比原图">
              <ActionIcon
                aria-label="按住对比原图"
                color={isComparingOriginal ? "cyan" : "gray"}
                disabled={!canCompare}
                onBlur={onCompareEnd}
                onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
                  if (event.key !== " " && event.key !== "Enter") return;
                  event.preventDefault();
                  onCompareStart?.();
                }}
                onKeyUp={(event: KeyboardEvent<HTMLButtonElement>) => {
                  if (event.key !== " " && event.key !== "Enter") return;
                  event.preventDefault();
                  onCompareEnd?.();
                }}
                onPointerCancel={onCompareEnd}
                onPointerDown={(event: PointerEvent<HTMLButtonElement>) => {
                  event.preventDefault();
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                  onCompareStart?.();
                }}
                onPointerUp={(event: PointerEvent<HTMLButtonElement>) => {
                  event.preventDefault();
                  event.currentTarget.releasePointerCapture?.(event.pointerId);
                  onCompareEnd?.();
                }}
                size={isViewportOnlyToolbar && isCompactToolbar ? "sm" : "lg"}
                variant={isComparingOriginal ? "filled" : "subtle"}
              >
                <MdiSelectCompareIcon size={isViewportOnlyToolbar && isCompactToolbar ? 16 : 20} />
              </ActionIcon>
            </Tooltip>
          ) : null}

          {showLandmarkToggle ? (
            <>
              <ToolbarIconButton
                active={landmarksVisible}
                compact={isViewportOnlyToolbar && isCompactToolbar}
                disabled={!onToggleLandmarks}
                label={landmarksVisible ? "隐藏关键点" : "显示关键点"}
                onClick={onToggleLandmarks}
                testId="landmark-visibility-toggle"
              >
                {landmarksVisible ? (
                  <IconEyeOff size={isViewportOnlyToolbar && isCompactToolbar ? 16 : 20} />
                ) : (
                  <IconEye size={isViewportOnlyToolbar && isCompactToolbar ? 16 : 20} />
                )}
              </ToolbarIconButton>
              <ToolbarIconButton
                active={landmarkDebugMode}
                compact={isViewportOnlyToolbar && isCompactToolbar}
                disabled={!onToggleLandmarkDebugMode}
                label="Landmark debug"
                onClick={onToggleLandmarkDebugMode}
                testId="landmark-debug-toggle"
              >
                <IconBug size={isViewportOnlyToolbar && isCompactToolbar ? 16 : 20} />
              </ToolbarIconButton>
            </>
          ) : null}

          <Divider color="var(--kb-line)" orientation="vertical" />

          <Slider
            aria-label="缩放图像"
            data-testid="editor-zoom-slider"
            disabled={!onZoomChange}
            label={(value: number) => `${value}%`}
            max={220}
            min={40}
            onChange={(value: number) => onZoomChange?.(value / 100)}
            size="sm"
            step={5}
            style={{
              flex: isCompactToolbar ? "1 1 92px" : "1 1 120px",
              maxWidth: isCompactToolbar ? undefined : 180,
              minWidth: isCompactToolbar ? 76 : 96,
            }}
            value={zoomPercent}
          />
          <Text
            c="dimmed"
            fw={700}
            miw={isViewportOnlyToolbar && isCompactToolbar ? 32 : isCompactToolbar ? 34 : 44}
            size="xs"
            style={{ marginLeft: isCompactToolbar ? "auto" : undefined }}
            ta="center"
          >
            {zoomPercent}%
          </Text>
          <ToolbarIconButton compact={isViewportOnlyToolbar && isCompactToolbar} disabled={!onFit} label="适应画布" onClick={onFit}>
            <IconMaximize size={isViewportOnlyToolbar && isCompactToolbar ? 16 : 20} />
          </ToolbarIconButton>
        </Group>
      ) : null}
    </Group>
  );
}

function ToolbarIconButton({
  active = false,
  children,
  compact = false,
  disabled,
  label,
  onClick,
  testId,
}: {
  active?: boolean;
  children: ReactNode;
  compact?: boolean;
  disabled?: boolean;
  label: string;
  onClick?: () => void;
  testId?: string;
}) {
  return (
    <Tooltip label={label}>
      <ActionIcon
        aria-label={label}
        color={active ? "cyan" : "gray"}
        data-testid={testId}
        disabled={disabled}
        onClick={onClick}
        size={compact ? "sm" : "lg"}
        variant={active ? "light" : "subtle"}
      >
        {children}
      </ActionIcon>
    </Tooltip>
  );
}

function MdiSelectCompareIcon({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" fill="currentColor" height={size} viewBox="0 0 24 24" width={size}>
      <path d="M4 4h7v2H6v5H4V4m9 0h7v7h-2V6h-5V4M4 13h2v5h5v2H4v-7m14 0h2v7h-7v-2h5v-5M8 8h8v8H8V8m2 2v4h4v-4h-4Z" />
    </svg>
  );
}
