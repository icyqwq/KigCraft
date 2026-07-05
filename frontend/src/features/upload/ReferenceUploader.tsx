import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Group,
  Image,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
  useMediaQuery,
} from "../../ui/mui";
import { IconPhoto, IconTrash, IconUpload } from "@tabler/icons-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type DragEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import type { ReferenceSlot } from "../workflow/workflowTypes";
import { FrontReferenceCropper } from "./FrontReferenceCropper";

export type ReferenceSlotId = "front" | "side" | "back" | "expression" | "accessory";

export type ReferenceSlotUpload = {
  objectKey: string;
  fileName: string;
  previewUrl?: string;
  file?: File;
};

export type ReferenceSlots = Partial<Record<ReferenceSlotId, ReferenceSlotUpload | null>>;

type SlotDefinition = {
  id: ReferenceSlotId;
  label: string;
  purpose: string;
  required: boolean;
};

type ReferenceSlotStyle = CSSProperties & {
  "--reference-slot-bg": string;
  "--reference-slot-border-color": string;
  "--reference-slot-border-style": string;
};

export type ReferenceUploaderProps = {
  headerAction?: ReactNode;
  referenceSlots?: ReferenceSlot[];
  onReferenceSlotsChange?: (slots: ReferenceSlot[]) => void;
  slots?: ReferenceSlots;
  onSlotsChange?: (slots: ReferenceSlots) => void;
  referenceNames?: string[];
  onReferenceNamesChange?: (names: string[]) => void;
};

const ACCEPTED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

const SLOT_DEFINITIONS: SlotDefinition[] = [
  { id: "front", label: "正脸参考", purpose: "脸型与五官比例", required: true },
  { id: "side", label: "侧面", purpose: "侧面轮廓", required: false },
  { id: "back", label: "背面", purpose: "后脑勺与背面发型", required: false },
  { id: "expression", label: "表情", purpose: "眼神、嘴型、情绪", required: false },
  { id: "accessory", label: "发饰/道具", purpose: "配件和特殊元素", required: false },
];

function isReferenceSlotId(value: string): value is ReferenceSlotId {
  return SLOT_DEFINITIONS.some((slot) => slot.id === value);
}

function namesToSlots(referenceNames: string[]): ReferenceSlots {
  return referenceNames.slice(0, SLOT_DEFINITIONS.length).reduce<ReferenceSlots>((nextSlots, name, index) => {
    const separatorIndex = name.indexOf(":");
    if (separatorIndex > 0) {
      const slotId = name.slice(0, separatorIndex);
      const objectKey = name.slice(separatorIndex + 1);
      if (isReferenceSlotId(slotId) && objectKey) {
        nextSlots[slotId] = { objectKey, fileName: objectKey };
        return nextSlots;
      }
    }

    const slot = SLOT_DEFINITIONS[index];
    if (slot) {
      nextSlots[slot.id] = { objectKey: name, fileName: name };
    }
    return nextSlots;
  }, {});
}

function referenceSlotListToSlots(referenceSlots: ReferenceSlot[]): ReferenceSlots {
  return referenceSlots.reduce<ReferenceSlots>((nextSlots, slot) => {
    if (!isReferenceSlotId(slot.kind)) return nextSlots;

    nextSlots[slot.kind] = slot.objectKey
      ? {
          objectKey: slot.objectKey,
          fileName: slot.fileName ?? slot.objectKey,
          previewUrl: slot.previewUrl,
          file: slot.file,
        }
      : null;
    return nextSlots;
  }, {});
}

function slotsToNames(slots: ReferenceSlots): string[] {
  return SLOT_DEFINITIONS.flatMap((slot) => {
    const upload = slots[slot.id];
    return upload ? [`${slot.id}:${upload.objectKey}`] : [];
  });
}

function slotsToReferenceSlotList(slots: ReferenceSlots): ReferenceSlot[] {
  return SLOT_DEFINITIONS.map((slot) => {
    const upload = slots[slot.id];
    return {
      kind: slot.id,
      label: slot.label,
      required: slot.required,
      fileName: upload?.fileName,
      objectKey: upload?.objectKey,
      previewUrl: upload?.previewUrl,
      file: upload?.file,
    };
  });
}

function revokeObjectUrl(url: string) {
  if (typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
}

function isObjectUrl(url: string) {
  return url.startsWith("blob:");
}

export function ReferenceUploader({
  headerAction,
  referenceSlots,
  onReferenceSlotsChange,
  slots,
  onSlotsChange,
  referenceNames,
  onReferenceNamesChange,
}: ReferenceUploaderProps) {
  const derivedSlots = useMemo(() => {
    if (referenceSlots) return referenceSlotListToSlots(referenceSlots);
    return slots ?? namesToSlots(referenceNames ?? []);
  }, [referenceNames, referenceSlots, slots]);
  const createdPreviewUrls = useRef(new Set<string>());
  const isExternallyControlled = Boolean(referenceSlots || slots);
  const isCompact = useMediaQuery("(max-width: 600px)");
  const [pendingFrontCrop, setPendingFrontCrop] = useState<File | null>(null);
  const [pendingLegacyFiles, setPendingLegacyFiles] = useState<File[] | null>(null);

  useEffect(
    () => () => {
      if (isExternallyControlled) return;
      createdPreviewUrls.current.forEach((url) => revokeObjectUrl(url));
      createdPreviewUrls.current.clear();
    },
    [isExternallyControlled],
  );

  useEffect(() => {
    const activePreviewUrls = new Set(
      Object.values(derivedSlots)
        .map((upload) => upload?.previewUrl)
        .filter((previewUrl): previewUrl is string => Boolean(previewUrl)),
    );

    createdPreviewUrls.current.forEach((url) => {
      if (!activePreviewUrls.has(url)) {
        revokeObjectUrl(url);
        createdPreviewUrls.current.delete(url);
      }
    });
  }, [derivedSlots]);

  function createPreviewUrl(file: File) {
    if (typeof URL.createObjectURL !== "function") return undefined;
    const previewUrl = URL.createObjectURL(file);
    createdPreviewUrls.current.add(previewUrl);
    return previewUrl;
  }

  function revokePreviewUrl(upload?: ReferenceSlotUpload | null) {
    if (!upload?.previewUrl || (!createdPreviewUrls.current.has(upload.previewUrl) && !isObjectUrl(upload.previewUrl))) {
      return;
    }
    revokeObjectUrl(upload.previewUrl);
    createdPreviewUrls.current.delete(upload.previewUrl);
  }

  function emitSlots(nextSlots: ReferenceSlots) {
    onReferenceSlotsChange?.(slotsToReferenceSlotList(nextSlots));
    onSlotsChange?.(nextSlots);
    onReferenceNamesChange?.(slotsToNames(nextSlots));
  }

  function updateSlot(slotId: ReferenceSlotId, upload: ReferenceSlotUpload | null) {
    emitSlots({ ...derivedSlots, [slotId]: upload });
  }

  function handleDrop(slotId: ReferenceSlotId, files: File[]) {
    const file = files[0];
    if (!file) return;

    if (slotId === "front") {
      setPendingLegacyFiles(null);
      setPendingFrontCrop(file);
      return;
    }

    revokePreviewUrl(derivedSlots[slotId]);
    updateSlot(slotId, {
      objectKey: file.name,
      fileName: file.name,
      previewUrl: createPreviewUrl(file),
      file,
    });
  }

  function handleSlotDrop(event: DragEvent<HTMLElement>, slotId: ReferenceSlotId) {
    event.preventDefault();
    handleDrop(slotId, Array.from(event.dataTransfer.files));
  }

  function handleLegacyInput(files: FileList | null) {
    const allFiles = Array.from(files ?? []);
    if (allFiles[0]) {
      setPendingLegacyFiles(allFiles);
      setPendingFrontCrop(allFiles[0]);
      return;
    }

    SLOT_DEFINITIONS.forEach((slot) => revokePreviewUrl(derivedSlots[slot.id]));
    const nextSlots = allFiles
      .slice(0, SLOT_DEFINITIONS.length)
      .reduce<ReferenceSlots>((next, file, index) => {
        const slot = SLOT_DEFINITIONS[index];
        if (slot) {
          next[slot.id] = {
            objectKey: file.name,
            fileName: file.name,
            previewUrl: createPreviewUrl(file),
            file,
          };
        }
        return next;
      }, {});

    emitSlots(nextSlots);
  }

  function confirmFrontCrop(croppedFile: File) {
    revokePreviewUrl(derivedSlots.front);
    const croppedUpload = {
      objectKey: croppedFile.name,
      fileName: croppedFile.name,
      previewUrl: createPreviewUrl(croppedFile),
      file: croppedFile,
    };

    if (pendingLegacyFiles) {
      SLOT_DEFINITIONS.forEach((slot) => revokePreviewUrl(derivedSlots[slot.id]));
      const nextSlots = pendingLegacyFiles
        .slice(0, SLOT_DEFINITIONS.length)
        .reduce<ReferenceSlots>((next, file, index) => {
          const slot = SLOT_DEFINITIONS[index];
          if (!slot) return next;
          if (slot.id === "front") {
            next.front = croppedUpload;
          } else {
            next[slot.id] = {
              objectKey: file.name,
              fileName: file.name,
              previewUrl: createPreviewUrl(file),
              file,
            };
          }
          return next;
        }, {});
      emitSlots(nextSlots);
    } else {
      updateSlot("front", croppedUpload);
    }

    setPendingFrontCrop(null);
    setPendingLegacyFiles(null);
  }

  function cancelFrontCrop() {
    setPendingFrontCrop(null);
    setPendingLegacyFiles(null);
  }

  function handleRemove(event: MouseEvent<HTMLElement>, slotId: ReferenceSlotId) {
    event.preventDefault();
    event.stopPropagation();
    revokePreviewUrl(derivedSlots[slotId]);
    updateSlot(slotId, null);
  }

  function renderSlot(slot: SlotDefinition) {
    const upload = derivedSlots[slot.id];
    const isMissingRequired = slot.required && !upload;
    const slotStyle = {
      "--reference-slot-bg": upload ? "var(--kb-panel-soft)" : "var(--kb-panel)",
      "--reference-slot-border-color": isMissingRequired ? "var(--kb-dirty-yellow)" : "var(--kb-line)",
      "--reference-slot-border-style": isMissingRequired ? "dashed" : "solid",
      cursor: "pointer",
      display: "flex",
      flexDirection: "column",
      minHeight: isCompact ? 132 : 152,
      overflow: "hidden",
      position: "relative",
    } satisfies ReferenceSlotStyle;

    return (
      <Box
        key={slot.id}
        className="reference-slot-card"
        component="label"
        data-testid={`reference-slot-${slot.id}`}
        onDragOver={(event: DragEvent<HTMLElement>) => event.preventDefault()}
        onDrop={(event: DragEvent<HTMLElement>) => handleSlotDrop(event, slot.id)}
        p={isCompact ? 1.5 : 2}
        radius={0}
        style={slotStyle}
      >
        <input
          accept={ACCEPTED_IMAGE_TYPES.join(",")}
          aria-label={`${slot.label}上传`}
          hidden
          onChange={(event: ChangeEvent<HTMLInputElement>) =>
            handleDrop(slot.id, Array.from(event.currentTarget.files ?? []))
          }
          type="file"
        />

        <Stack gap={isCompact ? 1 : 1.5} h="100%">
          <Group align="flex-start" justify="space-between" wrap="nowrap">
            <Group align="flex-start" gap={1.25} wrap="nowrap" style={{ minWidth: 0 }}>
              {!isCompact ? (
                <ThemeIcon color={upload ? "teal" : "cyan"} radius="md" size={36} variant="light">
                  {upload ? <IconPhoto size={20} /> : <IconUpload size={20} />}
                </ThemeIcon>
              ) : null}
              <Box style={{ minWidth: 0 }}>
                <Group align="center" gap={0.75} wrap={isCompact ? "wrap" : "nowrap"}>
                  <Text fw={800} size="sm" style={{ lineHeight: 1.25, whiteSpace: "nowrap" }}>
                    {slot.label}
                  </Text>
                  <Badge color={slot.required ? "cyan" : "gray"} size="xs" variant="light">
                    {slot.required ? "必填" : "可选"}
                  </Badge>
                </Group>
                {!isCompact ? (
                  <Text c="dimmed" mt={0.5} size="xs" style={{ lineHeight: 1.4 }}>
                    {slot.purpose}
                  </Text>
                ) : null}
              </Box>
            </Group>

            {upload ? (
              <ActionIcon
                aria-label={`删除${slot.label}`}
                color="red"
                onClick={(event: MouseEvent<HTMLElement>) => handleRemove(event, slot.id)}
                size="sm"
                variant="subtle"
              >
                <IconTrash size={16} />
              </ActionIcon>
            ) : null}
          </Group>

          {upload?.previewUrl ? (
            <Box
              mt="auto"
              radius="md"
              style={{
                alignItems: "center",
                aspectRatio: "16 / 10",
                background: "var(--kb-panel)",
                border: "2px solid var(--kb-line)",
                display: "flex",
                overflow: "hidden",
              }}
              title={upload.fileName}
            >
              <Image
                alt={`${slot.label}预览`}
                data-testid={`reference-preview-${slot.id}`}
                fit="cover"
                h="100%"
                src={upload.previewUrl}
                title={upload.fileName}
                w="100%"
              />
            </Box>
          ) : (
            <Box mt="auto">
              {isCompact ? (
                <ThemeIcon color="cyan" radius="md" size={30} variant="light">
                  <IconUpload size={17} />
                </ThemeIcon>
              ) : null}
              <Text fw={800} size="sm">
                {isCompact ? "点击上传" : "拖拽或点击上传"}
              </Text>
            </Box>
          )}
        </Stack>
      </Box>
    );
  }

  return (
    <Paper className="grunge-card" p={{ base: 2, md: 3 }} shadow="sm" withBorder>
      <Stack gap={2.5}>
        <Group align="center" gap={2} justify="space-between">
          <Box>
            <Title order={2} size="h3">
              上传参考图
            </Title>
            <Text c="dimmed" mt={0.75} size="sm">
              最多 5 张，正脸必填。
            </Text>
            <Text c="dimmed" mt={0.75} size="sm">
              参考图请尽量只包含头部或面部，避免背景、身体、文字和杂乱元素干扰生成质量。
            </Text>
          </Box>

          {headerAction ?? (
            <Button
              component="label"
              data-testid="reference-upload-button"
              leftSection={<IconUpload size={17} />}
              variant="filled"
            >
              上传
              <input
                accept={ACCEPTED_IMAGE_TYPES.join(",")}
                aria-label="上传参考图"
                hidden
                multiple
                onChange={(event: ChangeEvent<HTMLInputElement>) => handleLegacyInput(event.currentTarget.files)}
                type="file"
              />
            </Button>
          )}
        </Group>

        <SimpleGrid
          cols={{ base: 2, md: 5 }}
          data-slot-count={SLOT_DEFINITIONS.length}
          data-testid="reference-upload-grid"
          spacing={1.5}
        >
          {SLOT_DEFINITIONS.map((slot) => renderSlot(slot))}
        </SimpleGrid>

        {pendingFrontCrop ? (
          <FrontReferenceCropper file={pendingFrontCrop} onCancel={cancelFrontCrop} onConfirm={confirmFrontCrop} />
        ) : null}
      </Stack>
    </Paper>
  );
}
