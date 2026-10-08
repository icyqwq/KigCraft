import { Alert, Paper, Stack, Textarea } from "../../ui/mui";
import type { ChangeEvent } from "react";
import type { RequirementOption } from "../../api/client";

export type PromptComposerProps = {
  freeText: string;
  selectedRequirementIds?: string[];
  onFreeTextChange: (value: string) => void;
  onSelectedRequirementIdsChange?: (ids: string[]) => void;
  options?: RequirementOption[];
  selectedChipIds?: string[];
  onSelectedChipIdsChange?: (ids: string[]) => void;
};

function isPromptLong(value: string) {
  const chinese = (value.match(/[\u3400-\u9fff]/g) ?? []).length;
  const englishWords = value.trim().match(/[A-Za-z]+(?:['-][A-Za-z]+)*/g)?.length ?? 0;
  return chinese > 300 || englishWords > 600;
}

export function PromptComposer({
  freeText,
  onFreeTextChange,
}: PromptComposerProps) {
  return (
    <Paper className="grunge-card" p={{ base: 2, md: 3 }} shadow="sm" withBorder>
      <Stack gap={2.25}>
        <Textarea
          maxRows={4}
          minRows={2}
          onChange={(event: ChangeEvent<HTMLInputElement>) => onFreeTextChange(event.currentTarget.value)}
          placeholder="描述表情、发型、发饰，或需要保留/调整的细节"
          value={freeText}
        />
        {isPromptLong(freeText) ? (
          <Alert color="yellow" role="status" variant="light">
            提示词较长：超过 300 个中文字符或 600 个英文单词时，生成效果可能不稳定。
          </Alert>
        ) : null}
      </Stack>
    </Paper>
  );
}
