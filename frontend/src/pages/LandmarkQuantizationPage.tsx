import { Box, Button, Group, Paper, Stack, Text, Title } from "../ui/mui";
import { IconPhoto, IconRefresh } from "@tabler/icons-react";
import { useState, type ChangeEvent } from "react";
import {
  detectAnimeLandmarksWithModel,
  type AnimeLandmarkDebugInfo,
} from "../features/editor/deformation/animeLandmarkDetector";

const int8ModelUrl = "/models/anime-face-hrnetv2-int8.onnx";
const uploadInputId = "landmark-quantization-upload-input";

type DetectionResult = {
  debug: AnimeLandmarkDebugInfo | null;
  error?: string;
  label: string;
  modelUrl: string;
  timeMs: number;
};

function loadImage(url: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("图片加载失败"));
    image.src = url;
  });
}

function formatMs(ms: number) {
  if (!Number.isFinite(ms)) return "-";
  return `${Math.round(ms)} ms`;
}

function LandmarkOverlay({ imageUrl, result }: { imageUrl: string; result: DetectionResult }) {
  const debug = result.debug;

  return (
    <Paper p={2} style={{ border: "2px solid var(--kb-line)", borderRadius: 0 }} withBorder>
      <Stack gap={1.5}>
        <Group justify="space-between" wrap="nowrap">
          <Box>
            <Title order={3} size="h4">
              {result.label}
            </Title>
            <Text c="dimmed" size="sm">
              {result.modelUrl}
            </Text>
          </Box>
          <Text c="cyan.3" fw={800}>
            {formatMs(result.timeMs)}
          </Text>
        </Group>
        {result.error ? (
          <Text c="red" fw={700}>
            {result.error}
          </Text>
        ) : null}
        <Box
          style={{
            background: "#fff",
            border: "2px solid var(--kb-line)",
            position: "relative",
            width: "100%",
          }}
        >
          <Box
            alt=""
            component="img"
            src={imageUrl}
            style={{
              display: "block",
              height: "auto",
              width: "100%",
            }}
          />
          {debug ? (
            <>
              <Box
                style={{
                  border: "2px dashed #22c55e",
                  height: `${debug.faceBox.height / debug.imageHeight * 100}%`,
                  left: `${debug.faceBox.x / debug.imageWidth * 100}%`,
                  pointerEvents: "none",
                  position: "absolute",
                  top: `${debug.faceBox.y / debug.imageHeight * 100}%`,
                  width: `${debug.faceBox.width / debug.imageWidth * 100}%`,
                }}
              />
              <Box
                style={{
                  border: "2px dashed #38bdf8",
                  height: `${debug.hrnetBox.height / debug.imageHeight * 100}%`,
                  left: `${debug.hrnetBox.x / debug.imageWidth * 100}%`,
                  pointerEvents: "none",
                  position: "absolute",
                  top: `${debug.hrnetBox.y / debug.imageHeight * 100}%`,
                  width: `${debug.hrnetBox.width / debug.imageWidth * 100}%`,
                }}
              />
              {debug.points.map((point) => (
                <Box
                  key={point.index}
                  title={`${point.index}: ${point.x.toFixed(1)}, ${point.y.toFixed(1)}, score ${point.score.toFixed(3)}`}
                  style={{
                    alignItems: "center",
                    background: "#facc15",
                    border: "1px solid #111827",
                    borderRadius: 999,
                    color: "#111827",
                    display: "flex",
                    fontSize: 10,
                    fontWeight: 800,
                    height: 18,
                    justifyContent: "center",
                    left: `${point.x / debug.imageWidth * 100}%`,
                    lineHeight: 1,
                    pointerEvents: "none",
                    position: "absolute",
                    top: `${point.y / debug.imageHeight * 100}%`,
                    transform: "translate(-50%, -50%)",
                    width: 18,
                  }}
                >
                  {point.index}
                </Box>
              ))}
            </>
          ) : null}
        </Box>
      </Stack>
    </Paper>
  );
}

export function LandmarkQuantizationPage() {
  const [imageUrl, setImageUrl] = useState("");
  const [results, setResults] = useState<DetectionResult[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  async function runModel(label: string, modelUrl: string, image: HTMLImageElement): Promise<DetectionResult> {
    const startedAt = performance.now();
    try {
      const detection = await detectAnimeLandmarksWithModel(image, modelUrl);
      return {
        debug: detection?.debug ?? null,
        label,
        modelUrl,
        timeMs: performance.now() - startedAt,
      };
    } catch (error: unknown) {
      return {
        debug: null,
        error: error instanceof Error ? error.message : String(error),
        label,
        modelUrl,
        timeMs: performance.now() - startedAt,
      };
    }
  }

  async function runComparison() {
    if (!imageUrl) return;
    setIsRunning(true);
    try {
      const image = await loadImage(imageUrl);
      const int8 = await runModel("INT8 量化模型", int8ModelUrl, image);
      setResults([int8]);
    } finally {
      setIsRunning(false);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setResults([]);
    setImageUrl(URL.createObjectURL(file));
    event.currentTarget.value = "";
  }

  return (
    <Box
      p={{ base: 2, md: 4 }}
      style={{
        background: "var(--kb-bg)",
        minHeight: "100dvh",
      }}
    >
      <Stack gap={2.5} maw={1440} mx="auto">
        <Paper p={3} style={{ border: "3px solid var(--kb-line)", borderRadius: 0 }} withBorder>
          <Stack gap={1.5}>
            <Title order={1}>HRNet INT8 测试</Title>
            <Text c="dimmed">
              用当前前端实际加载的 INT8 模型运行 landmark 识别，检查初始化/推理耗时和 28 个 landmark 点位。
            </Text>
            <Group gap={1} wrap="wrap">
              <Button disabled={!imageUrl} leftSection={<IconRefresh size={16} />} loading={isRunning} onClick={() => void runComparison()}>
                开始对比
              </Button>
              <Button color="gray" component="label" htmlFor={uploadInputId} leftSection={<IconPhoto size={16} />} variant="light">
                上传测试图
              </Button>
              <input
                accept="image/png,image/jpeg,image/webp"
                id={uploadInputId}
                onChange={handleFileChange}
                style={{ height: 1, opacity: 0, position: "absolute", width: 1 }}
                tabIndex={-1}
                type="file"
              />
              <Button
                color="gray"
                onClick={() => {
                  setResults([]);
                  setImageUrl("");
                }}
                variant="light"
              >
                使用 fixture
              </Button>
            </Group>
            <Group gap={2} wrap="wrap">
              <Text c="dimmed" size="sm">
                INT8: 10.4 MB
              </Text>
            </Group>
          </Stack>
        </Paper>

        <Box
          style={{
            display: "grid",
            gap: 16,
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          }}
        >
          {results.length > 0 ? (
            results.map((result) => <LandmarkOverlay imageUrl={imageUrl} key={result.modelUrl} result={result} />)
          ) : (
            <Paper p={3} style={{ border: "2px solid var(--kb-line)", borderRadius: 0 }} withBorder>
              <Text c="dimmed">点击“开始对比”后会显示 INT8 landmark 结果。</Text>
            </Paper>
          )}
        </Box>
      </Stack>
    </Box>
  );
}
