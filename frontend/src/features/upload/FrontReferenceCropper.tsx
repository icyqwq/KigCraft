import { IconCheck, IconEraser, IconPencil, IconTrash, IconX } from "@tabler/icons-react";
import { useEffect, useRef, useState, type DragEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { Alert, Box, Button, Group, Paper, Slider, Stack, Text, Title } from "../../ui/mui";
import { LocalMaskLayer } from "../editor/components/LocalMaskLayer";
import { detectAnimeFaceBox } from "../editor/deformation/animeLandmarkDetector";
import type { LocalMaskMode, LocalMaskStroke } from "../editor/localGeneration";
import {
  createFaceBoxMaskStrokes,
  cropImageFileWithMask,
  isReliableFaceBoxDetection,
  loadImageFromFile,
} from "./frontReferenceCrop";

export type FrontReferenceCropperProps = {
  file: File;
  onCancel: () => void;
  onConfirm: (file: File) => void;
};

export function FrontReferenceCropper({ file, onCancel, onConfirm }: FrontReferenceCropperProps) {
  const [brushMode, setBrushMode] = useState<LocalMaskMode>("brush");
  const [brushRadius, setBrushRadius] = useState(36);
  const [error, setError] = useState<string | null>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [strokes, setStrokes] = useState<LocalMaskStroke[]>([]);
  const [brushPreview, setBrushPreview] = useState<{ radius: number; x: number; y: number } | null>(null);
  const activeStrokeIdRef = useRef<string | null>(null);
  const imageFrameRef = useRef<HTMLDivElement | null>(null);

  const imageWidth = image?.naturalWidth || image?.width || 1;
  const imageHeight = image?.naturalHeight || image?.height || 1;
  const imageAspectRatio = imageWidth / imageHeight;
  const canConfirm = strokes.some((stroke) => stroke.mode === "brush" && stroke.points.length > 0) && !isBusy;

  useEffect(() => {
    let cancelled = false;
    const nextUrl = URL.createObjectURL(file);
    setImageUrl(nextUrl);
    setImage(null);
    setStrokes([]);
    setError(null);
    setIsBusy(true);

    void loadImageFromFile(file)
      .then(async (loadedImage) => {
        if (cancelled) return;
        setImage(loadedImage);
        const detection = await detectAnimeFaceBox(loadedImage);
        if (cancelled) return;
        const width = loadedImage.naturalWidth || loadedImage.width || 1;
        const height = loadedImage.naturalHeight || loadedImage.height || 1;
        if (detection && !detection.usedFallback && isReliableFaceBoxDetection(detection.box, width, height, detection.score)) {
          setStrokes(createFaceBoxMaskStrokes(detection.box, width, height));
        } else {
          setStrokes([]);
          setError("未识别到头部，请手动涂出头部。");
        }
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setError(loadError instanceof Error ? loadError.message : "头部图片读取失败。");
      })
      .finally(() => {
        if (!cancelled) setIsBusy(false);
      });

    return () => {
      cancelled = true;
      URL.revokeObjectURL(nextUrl);
    };
  }, [file]);

  function pointFromEvent(event: PointerEvent<HTMLElement>) {
    const rect = imageFrameRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: clamp((event.clientX - rect.left) / rect.width),
      y: clamp((event.clientY - rect.top) / rect.height),
    };
  }

  function beginStroke(event: PointerEvent<HTMLElement>) {
    const point = pointFromEvent(event);
    if (!point) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const id = `front-mask-${Date.now()}-${strokes.length}`;
    activeStrokeIdRef.current = id;
    setBrushPreview({ radius: brushRadius, ...point });
    setStrokes((current) => [
      ...current,
      {
        id,
        mode: brushMode,
        points: [point],
        radius: brushRadius,
      },
    ]);
  }

  function appendStroke(event: PointerEvent<HTMLElement>) {
    const point = pointFromEvent(event);
    if (!point) return;
    setBrushPreview({ radius: brushRadius, ...point });
    const activeStrokeId = activeStrokeIdRef.current;
    if (!activeStrokeId) return;
    setStrokes((current) =>
      current.map((stroke) => (stroke.id === activeStrokeId ? { ...stroke, points: [...stroke.points, point] } : stroke)),
    );
  }

  function endStroke(event: PointerEvent<HTMLElement>) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    activeStrokeIdRef.current = null;
  }

  async function confirmCrop() {
    if (!canConfirm) return;
    setIsBusy(true);
    setError(null);
    try {
      onConfirm(await cropImageFileWithMask(file, strokes));
    } catch (cropError: unknown) {
      setError(cropError instanceof Error ? cropError.message : "头部裁剪失败。");
    } finally {
      setIsBusy(false);
    }
  }

  const content = (
    <Box
      data-testid="front-reference-cropper-overlay"
      style={{
        alignItems: "center",
        background: "rgba(25, 31, 35, 0.46)",
        display: "flex",
        inset: 0,
        justifyContent: "center",
        padding: "24px",
        position: "fixed",
        zIndex: 1400,
      }}
    >
      <Paper
        aria-labelledby="front-reference-crop-title"
        aria-modal="true"
        className="grunge-card"
        data-testid="front-reference-cropper"
        p={{ base: 2, md: 3 }}
        role="dialog"
        shadow="sm"
        style={{
          maxHeight: "calc(100vh - 48px)",
          maxWidth: "min(1280px, calc(100vw - 48px))",
          overflow: "auto",
          width: "100%",
        }}
        withBorder
      >
      <Stack gap={2}>
        <Group align="flex-start" justify="space-between" wrap="wrap">
          <Box>
            <Title id="front-reference-crop-title" order={3} size="h4">
              确认头部
            </Title>
            <Text c="dimmed" size="sm">
              涂出头部、发型和发饰，系统会裁掉四周多余区域作为头部参考。
            </Text>
          </Box>
          <Group gap={1}>
            <Button leftSection={<IconX size={16} />} onClick={onCancel} size="sm" variant="light">
              取消
            </Button>
            <Button
              data-testid="front-reference-crop-confirm"
              disabled={!canConfirm}
              leftSection={<IconCheck size={16} />}
              loading={isBusy}
              onClick={() => void confirmCrop()}
              size="sm"
              variant="filled"
            >
              确认
            </Button>
          </Group>
        </Group>

        {error ? (
          <Alert color="yellow" data-testid="front-reference-crop-warning" variant="light">
            {error}
          </Alert>
        ) : null}

        <Box
          data-testid="front-reference-crop-stage"
          onDragStart={(event: DragEvent<HTMLElement>) => event.preventDefault()}
          onPointerCancel={endStroke}
          onPointerDown={beginStroke}
          onPointerLeave={() => {
            activeStrokeIdRef.current = null;
            setBrushPreview(null);
          }}
          onPointerMove={appendStroke}
          onPointerUp={endStroke}
          ref={imageFrameRef}
          style={{
            aspectRatio: `${imageWidth} / ${imageHeight}`,
            background: "var(--kb-panel-soft)",
            border: "3px solid var(--kb-line)",
            margin: "0 auto",
            maxHeight: "min(62vh, 620px)",
            maxWidth: "100%",
            overflow: "hidden",
            position: "relative",
            touchAction: "none",
            width: `min(100%, calc(min(62vh, 620px) * ${imageAspectRatio}))`,
          }}
        >
          {imageUrl ? (
            <Box
              alt="头部参考裁剪预览"
              component="img"
              draggable={false}
              onDragStart={(event: DragEvent<HTMLElement>) => event.preventDefault()}
              src={imageUrl}
              style={{
                display: "block",
                height: "100%",
                objectFit: "contain",
                pointerEvents: "none",
                userSelect: "none",
                width: "100%",
              }}
            />
          ) : null}
          <LocalMaskLayer brushPreview={brushPreview} height={imageHeight} strokes={strokes} width={imageWidth} />
        </Box>

        <Group align="center" gap={1} wrap="wrap">
          <Button
            leftSection={<IconPencil size={16} />}
            onClick={() => setBrushMode("brush")}
            size="xs"
            variant={brushMode === "brush" ? "filled" : "light"}
          >
            笔刷
          </Button>
          <Button
            leftSection={<IconEraser size={16} />}
            onClick={() => setBrushMode("erase")}
            size="xs"
            variant={brushMode === "erase" ? "filled" : "light"}
          >
            橡皮
          </Button>
          <Button leftSection={<IconTrash size={16} />} onClick={() => setStrokes([])} size="xs" variant="light">
            清空
          </Button>
          <Text c="dimmed" size="sm">
            笔刷
          </Text>
          <Slider
            data-testid="front-reference-crop-radius"
            max={120}
            min={8}
            onChange={setBrushRadius}
            step={2}
            style={{ flex: "1 1 220px" }}
            value={brushRadius}
          />
          <Text c="dimmed" miw={32} size="sm" ta="right">
            {brushRadius}
          </Text>
        </Group>
      </Stack>
      </Paper>
    </Box>
  );

  return createPortal(content, document.body);
}

function clamp(value: number) {
  return Math.min(1, Math.max(0, value));
}
