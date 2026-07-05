import { IconEdit, IconEye, IconMoodSmile, IconSparkles, IconUserCircle, IconWand } from "@tabler/icons-react";
import { Box, Button, Stack, Tab, Tabs, Text } from "../../../ui/mui";

export type EditorTool = "annotation" | "face" | "eyes" | "mouth" | "liquify" | "local-generate";

type EditorToolConfig = {
  key: EditorTool;
  label: string;
  icon: typeof IconEdit;
};

const editorTools: EditorToolConfig[] = [
  { key: "annotation", label: "标注", icon: IconEdit },
  { key: "face", label: "脸型", icon: IconUserCircle },
  { key: "eyes", label: "眼睛", icon: IconEye },
  { key: "mouth", label: "嘴巴", icon: IconMoodSmile },
  { key: "liquify", label: "液化", icon: IconWand },
  { key: "local-generate", label: "局部生成", icon: IconSparkles },
];

export type EditorToolRailProps = {
  activeTool: EditorTool;
  orientation?: "horizontal" | "vertical";
  tools?: EditorTool[];
  onToolChange: (tool: EditorTool) => void;
};

export function EditorToolRail({ activeTool, orientation = "vertical", tools, onToolChange }: EditorToolRailProps) {
  const isHorizontal = orientation === "horizontal";
  const allowedTools = tools ? new Set(tools) : null;
  const visibleTools = editorTools.filter((tool) => !allowedTools || allowedTools.has(tool.key));
  const buttons = visibleTools.map((tool) => {
    const ToolIcon = tool.icon;
    const selected = tool.key === activeTool;

    return (
      <Button
        key={tool.key}
        aria-pressed={selected}
        className={`editor-tool-button ${isHorizontal ? "editor-tool-button-horizontal" : "editor-tool-button-vertical"}`}
        color="gray"
        data-testid={`editor-tool-${tool.key}`}
        fullWidth={!isHorizontal}
        justify={isHorizontal ? "center" : "flex-start"}
        leftSection={<ToolIcon size={17} />}
        onClick={() => onToolChange(tool.key)}
        px={isHorizontal ? 6 : "sm"}
        size="sm"
        style={{
          ...(isHorizontal ? { flex: "0 0 auto", minWidth: 68 } : { minHeight: 38 }),
          backgroundColor: selected ? "var(--kb-dirty-yellow)" : "var(--kb-panel)",
          borderColor: "var(--kb-line)",
          color: selected ? "var(--kb-off-white)" : "var(--kb-ink)",
        }}
        variant="light"
      >
        {tool.label}
      </Button>
    );
  });

  return (
    <Stack gap="xs">
      <Text c="dimmed" fw={700} size="xs" style={isHorizontal ? { display: "none" } : undefined} tt="uppercase">
        工具
      </Text>
      {isHorizontal ? (
        <Box style={{ minWidth: 0 }}>
          <Tabs
            aria-label="编辑工具"
            onChange={(_event, value) => onToolChange(value as EditorTool)}
            sx={{
              minHeight: 58,
              "& .MuiTabs-indicator": {
                height: 3,
              },
              "& .MuiTab-root": {
                borderBottom: "2px solid var(--kb-line)",
                color: "var(--kb-muted)",
                fontSize: "0.78rem",
                fontWeight: 800,
                letterSpacing: 0,
                lineHeight: 1.05,
                minHeight: 58,
                minWidth: 0,
                overflow: "hidden",
                padding: "6px 2px 8px",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                wordBreak: "keep-all",
              },
              "& .MuiTab-iconWrapper": {
                marginBottom: "3px",
                marginRight: "0 !important",
              },
              "& .MuiTab-root.Mui-selected": {
                color: "var(--kb-ink)",
              },
            }}
            value={activeTool}
            variant="fullWidth"
          >
            {visibleTools.map((tool) => {
              const ToolIcon = tool.icon;
              return (
                <Tab
                  key={tool.key}
                  data-testid={`editor-tool-${tool.key}`}
                  icon={<ToolIcon size={17} />}
                  iconPosition="top"
                  label={tool.label}
                  value={tool.key}
                />
              );
            })}
          </Tabs>
        </Box>
      ) : (
        <Stack gap={0.75}>{buttons}</Stack>
      )}
    </Stack>
  );
}
