import { Badge, Box, Group, Image, Paper, Stack, Text, Title } from "../../ui/mui";
import { IconPhotoCheck, IconPhotoPlus } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { listAlbumItems } from "../../api/client";

export type AlbumSaveStatus = "idle" | "saving" | "saved" | "error";

export function AlbumPanel({
  savedCount,
  saveStatus = "idle",
  saveMessage,
}: {
  savedCount: number;
  saveStatus?: AlbumSaveStatus;
  saveMessage?: string | null;
}) {
  const albumQuery = useQuery({
    queryKey: ["album-items"],
    queryFn: listAlbumItems,
  });
  const items = albumQuery.data ?? [];
  const latestItem = items[0];
  const displayedCount = albumQuery.isSuccess ? items.length : savedCount;
  const statusText =
    saveMessage ??
    (saveStatus === "saving"
      ? "正在保存图片..."
      : saveStatus === "saved"
        ? "已保存到本地文件"
        : saveStatus === "error"
          ? "保存失败，请重试"
          : "保存按钮会导出当前编辑后的图片文件。");

  return (
    <Paper
      component="section"
      p="md"
      radius="sm"
      style={{
        background: "#0b0f18",
        border: "1px solid rgba(148, 163, 184, 0.18)",
      }}
    >
      <Stack gap="md">
        <Group align="flex-start" justify="space-between" wrap="nowrap">
          <Box>
            <Title c="white" order={2} size="h4">
              我的相册
            </Title>
            <Text c="dimmed" data-testid="album-saved-count" mt={4} size="sm">
              已保存 {displayedCount} 张结果
            </Text>
          </Box>
          <Badge
            color={saveStatus === "error" ? "red" : saveStatus === "saved" ? "green" : "cyan"}
            leftSection={saveStatus === "saved" ? <IconPhotoCheck size={14} /> : <IconPhotoPlus size={14} />}
            radius="sm"
            variant="light"
          >
            {saveStatus === "saving" ? "保存中" : saveStatus === "saved" ? "已保存" : "相册"}
          </Badge>
        </Group>

        {latestItem ? (
          <Box>
            <Image
              alt="最近保存的图片"
              fit="cover"
              h={160}
              radius="sm"
              src={latestItem.image_url}
              style={{ border: "1px solid rgba(148, 163, 184, 0.16)" }}
            />
            <Text c="dimmed" mt={6} size="xs">
              最近保存于 {new Date(latestItem.created_at).toLocaleString("zh-CN")}
            </Text>
          </Box>
        ) : (
          <Box
            p="md"
            style={{
              alignItems: "center",
              background: "rgba(15, 23, 42, 0.72)",
              border: "1px dashed rgba(148, 163, 184, 0.24)",
              borderRadius: 8,
              display: "flex",
              minHeight: 120,
            }}
          >
            <Text c="dimmed" size="sm">
              暂无保存图片。完成精修后，保存按钮会导出当前结果。
            </Text>
          </Box>
        )}

        <Text
          c={saveStatus === "error" ? "red.3" : saveStatus === "saved" ? "green.3" : "dimmed"}
          data-testid="album-save-status"
          size="sm"
        >
          {albumQuery.isError ? "相册记录加载失败。" : statusText}
        </Text>
      </Stack>
    </Paper>
  );
}
