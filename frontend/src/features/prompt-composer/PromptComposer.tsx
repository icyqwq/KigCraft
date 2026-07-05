import { Alert, Box, Button, Group, Paper, Stack, Text, Textarea, Title } from "../../ui/mui";
import { IconAlertCircle, IconRefresh } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, type ChangeEvent } from "react";
import { getRequirementOptions, type RequirementOption } from "../../api/client";

export type PromptComposerProps = {
  freeText: string;
  selectedRequirementIds?: string[];
  onFreeTextChange: (value: string) => void;
  onSelectedRequirementIdsChange?: (ids: string[]) => void;
  options?: RequirementOption[];
  selectedChipIds?: string[];
  onSelectedChipIdsChange?: (ids: string[]) => void;
};

type RequirementGroup = {
  group: string;
  options: RequirementOption[];
};

function groupRequirementOptions(options: RequirementOption[]): RequirementGroup[] {
  const groups = new Map<string, RequirementOption[]>();

  [...options]
    .sort((left, right) => left.sort_order - right.sort_order)
    .forEach((option) => {
      const groupOptions = groups.get(option.group) ?? [];
      groupOptions.push(option);
      groups.set(option.group, groupOptions);
    });

  return Array.from(groups, ([group, groupOptions]) => ({ group, options: groupOptions }));
}

function isHiddenFrontendGroup(group: string) {
  return group === "成品质感" || group.includes("系统固定");
}

function isHiddenFrontendOption(option: RequirementOption) {
  const normalizedLabel = option.label.trim().toLowerCase();
  const normalizedId = option.id.trim().toLowerCase();
  return normalizedLabel === "其它" || normalizedLabel === "其他" || normalizedId === "other";
}

export function PromptComposer({
  freeText,
  selectedRequirementIds,
  selectedChipIds,
  onFreeTextChange,
  onSelectedRequirementIdsChange,
  onSelectedChipIdsChange,
  options,
}: PromptComposerProps) {
  const requirements = useQuery({
    queryKey: ["prompt-requirements"],
    queryFn: getRequirementOptions,
    enabled: options === undefined,
  });
  const availableOptions = options ?? requirements.data ?? [];
  const selectableOptions = availableOptions.filter(
    (option) => !isHiddenFrontendGroup(option.group) && !isHiddenFrontendOption(option),
  );
  const groupedOptions = useMemo(() => groupRequirementOptions(selectableOptions), [selectableOptions]);
  const selectedIds = selectedRequirementIds ?? selectedChipIds ?? [];

  function updateSelectedIds(nextIds: string[]) {
    onSelectedRequirementIdsChange?.(nextIds);
    onSelectedChipIdsChange?.(nextIds);
  }

  function toggleOption(optionId: string) {
    const nextIds = selectedIds.includes(optionId)
      ? selectedIds.filter((id) => id !== optionId)
      : [...selectedIds, optionId];
    updateSelectedIds(nextIds);
  }

  return (
    <Paper className="grunge-card" p={{ base: 2, md: 3 }} shadow="sm" withBorder>
      <Stack gap={2.25}>
        <Box>
          <Title order={2} size="h3">
            生成要求
          </Title>
          <Text c="dimmed" mt={0.75} size="sm">
            选择要强调的外观、表情和发型。
          </Text>
        </Box>

        <Textarea
          maxRows={4}
          minRows={2}
          onChange={(event: ChangeEvent<HTMLInputElement>) => onFreeTextChange(event.currentTarget.value)}
          placeholder="描述表情、发型、发饰，或需要保留/调整的细节"
          value={freeText}
        />

        {requirements.isLoading && options === undefined ? (
          <Text c="dimmed" size="sm">
            正在加载生成要求选项...
          </Text>
        ) : null}

        {requirements.isError && options === undefined ? (
          <Alert color="red" icon={<IconAlertCircle size={18} />} role="alert" variant="light">
            <Group align="center" gap={1.5} justify="space-between">
              <Text size="sm">生成要求选项加载失败。</Text>
              <Button
                color="red"
                leftSection={<IconRefresh size={15} />}
                onClick={() => void requirements.refetch()}
                size="xs"
                variant="light"
              >
                重试
              </Button>
            </Group>
          </Alert>
        ) : null}

        {!requirements.isLoading && !requirements.isError && groupedOptions.length === 0 ? (
          <Text c="dimmed" size="sm">
            暂无可选生成要求。
          </Text>
        ) : null}

        {groupedOptions.length > 0 ? (
          <Stack gap={1.5}>
            {groupedOptions.map((group) => (
              <Box key={group.group}>
                <Text c="dimmed" fw={800} mb={0.75} size="xs" tt="uppercase">
                  {group.group}
                </Text>
                <Group gap={1} wrap="wrap">
                  {group.options.map((option) => {
                    const selected = selectedIds.includes(option.id);

                    return (
                      <Button
                        key={option.id}
                        aria-pressed={selected}
                        color="gray"
                        onClick={() => toggleOption(option.id)}
                        size="xs"
                        style={{
                          backgroundColor: selected ? "var(--kb-dirty-yellow)" : "var(--kb-panel)",
                          borderColor: "var(--kb-line)",
                          color: selected ? "var(--kb-off-white)" : "var(--kb-ink)",
                        }}
                        title={option.description}
                        variant="light"
                      >
                        {option.label}
                      </Button>
                    );
                  })}
                </Group>
              </Box>
            ))}
          </Stack>
        ) : null}
      </Stack>
    </Paper>
  );
}
