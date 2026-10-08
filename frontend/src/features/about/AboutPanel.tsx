import {useTranslation} from "react-i18next";
import {IconBrandGithub} from "@tabler/icons-react";
import {Badge,Box,Group,Paper,Stack,Text,Title} from "../../ui/mui";
import {GITHUB_URL} from "../../app/links";

const openSourceProjects = [
  { name: "fflate", purpose: "所选展示图打包", url: "https://github.com/101arrowz/fflate", version: "0.8.3" },
  { name: "React", purpose: "前端界面运行时", url: "https://react.dev/", version: "19.1.0" },
  { name: "Vite", purpose: "前端构建与开发服务", url: "https://vite.dev/", version: "7.0.0" },
  { name: "Material UI", purpose: "基础 UI 组件", url: "https://mui.com/material-ui/", version: "9.1.2" },
  { name: "Tabler Icons", purpose: "界面图标", url: "https://tabler.io/icons", version: "3.44.0" },
  {
    name: "TanStack Query",
    purpose: "服务端状态与轮询",
    url: "https://tanstack.com/query/latest",
    version: "5.81.5",
  },
  { name: "PixiJS", purpose: "图像编辑画布渲染", url: "https://pixijs.com/", version: "8.10.1" },
  { name: "Konva", purpose: "标注图形交互", url: "https://konvajs.org/", version: "10.3.0" },
  { name: "ONNX Runtime Web", purpose: "浏览器端模型推理", url: "https://onnxruntime.ai/", version: "1.27.0" },
  {
    name: "MediaPipe Tasks Vision",
    purpose: "图像视觉能力支持",
    url: "https://ai.google.dev/edge/mediapipe/solutions/vision",
    version: "0.10.35",
  },
  { name: "Zod", purpose: "数据结构校验", url: "https://zod.dev/", version: "3.25.67" },
] as const;
function formatBuildDate(value: string, locale: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    hour12: false,
    timeStyle: "short",
  }).format(date)}`;
}

export default function AboutPanel() {
  const { t, i18n } = useTranslation();

  return (
    <Stack gap={2}>
      <Paper className="grunge-card" p={{ base: 2, md: 3 }} withBorder>
        <Stack gap={2}>
          <Text c="dimmed">{t("v2Account.description")}</Text>
          <div className="v2-about-meta">
            <Badge color="cyan" variant="light">{t("v2Account.built")}</Badge>
            <strong>{formatBuildDate(__KIGCRAFT_BUILD_DATE__, i18n.language)}</strong>
          </div>
          <a className="v2-about-github" href={GITHUB_URL} target="_blank" rel="noreferrer">
            <IconBrandGithub size={18} stroke={1.75} aria-hidden="true" />{t("v2Account.github")}
          </a>
        </Stack>
      </Paper>

      <Paper className="grunge-card" p={{ base: 2, md: 3 }} withBorder>
        <Stack gap={2}>
          <Box>
            <Title order={2} size="h3">
              {t("v2Account.openSource")}
            </Title>
            <Text c="dimmed" mt={0.75} size="sm">
              {t("v2Account.licenses")}
            </Text>
          </Box>

          <Box
            style={{
              display: "grid",
              gap: 12,
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            }}
          >
            {openSourceProjects.map((project) => (
              <Paper
                key={project.name}
                p={2}
                style={{
                  background: "var(--kb-panel-soft)",
                  boxShadow: "var(--kb-hard-shadow-sm)",
                }}
                withBorder
              >
                <Stack gap={1}>
                  <Group align="flex-start" justify="space-between" wrap="nowrap">
                    <Box>
                      <Text
                        component="a"
                        fw={900}
                        href={project.url}
                        rel="noreferrer"
                        style={{ color: "inherit", textDecoration: "none" }}
                        target="_blank"
                      >
                        {project.name}
                      </Text>
                      <Text c="dimmed" size="sm">
                        {t("v2Account.purposes."+project.name)}
                      </Text>
                    </Box>
                    <Badge color="gray" variant="light">
                      {project.version}
                    </Badge>
                  </Group>
                </Stack>
              </Paper>
            ))}
          </Box>
        </Stack>
      </Paper>
    </Stack>
  );
}
