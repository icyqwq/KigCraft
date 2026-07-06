import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DetailAnalysis, GenerationJob, GenerationMode, JobEvent } from "../api/client";
import {
  ApiError,
  analyzeReferenceDetails,
  createGenerationJob,
  createLocalRevisionJob,
  getGenerationEvents,
  getGenerationJob,
  getRequirementOptions,
  uploadReferenceFile,
} from "../api/client";
import { kigTheme } from "../app/theme";
import i18n from "../i18n";
import { DEFAULT_LOCALE } from "../i18n/locales";
import { WorkflowPage, createEditorReferenceSlots, isTerminalGenerationStatus } from "./WorkflowPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    analyzeReferenceDetails: vi.fn(),
    createGenerationJob: vi.fn(),
    createLocalRevisionJob: vi.fn(),
    getGenerationEvents: vi.fn(),
    getGenerationJob: vi.fn(),
    getRequirementOptions: vi.fn(),
    uploadReferenceFile: vi.fn(),
  };
});

vi.mock("../features/editor/EditorWorkspace", () => ({
  EditorWorkspace: ({
    availableTools,
    candidateIndex,
    imageUrl,
    onRegenerate,
    onSecondaryRegenerate,
    onLocalGenerate,
    onClearImage,
  }: {
    availableTools?: string[];
    candidateIndex: number;
    imageUrl?: string;
    onRegenerate?: (payload: {
      annotationPrompt: string;
      editedImageBlob: Blob;
      promptNote?: string;
      recipe: { annotations: unknown[]; face: Record<string, number>; eyes: Record<string, number>; liquify: unknown[] };
    }) => void | Promise<void>;
    onSecondaryRegenerate?: (payload: {
      annotationPrompt: string;
      editedImageBlob: Blob;
      promptNote?: string;
      recipe: { annotations: unknown[]; face: Record<string, number>; eyes: Record<string, number>; liquify: unknown[] };
    }) => void | Promise<void>;
    onLocalGenerate?: (payload: {
      baseImageBlob: Blob;
      editNote: string;
      maskImageBlob: Blob;
      recipe: { annotations: unknown[]; face: Record<string, number>; eyes: Record<string, number>; liquify: unknown[] };
      selectedReferenceKeys: string[];
      uploadedReferences: Array<{ description: string; file: File }>;
    }) => void | Promise<void>;
    onClearImage?: () => void;
  }) => {
    const payload = {
      annotationPrompt: "",
      editedImageBlob: new Blob(["edited"], { type: "image/png" }),
      promptNote: "嘴型更委屈，眼神更柔和",
      recipe: { annotations: [], face: {}, eyes: {}, liquify: [] },
    };
    return (
      <section aria-label="editor-workspace">
        <h2>Editor candidate {candidateIndex}</h2>
        <p data-testid="editor-image-url">{imageUrl ?? "none"}</p>
        <p data-testid="editor-tools">{availableTools?.join(",") ?? "all"}</p>
        <button onClick={onClearImage} type="button">
          娓呯┖鍥剧墖
        </button>
        {onRegenerate ? (
          <button data-testid="editor-regenerate" onClick={() => void onRegenerate(payload)} type="button">
            regenerate
          </button>
        ) : null}
        {onSecondaryRegenerate ? (
          <button
            data-testid="editor-secondary-regenerate"
            onClick={() => void onSecondaryRegenerate(payload)}
            type="button"
          >
            secondary regenerate
          </button>
        ) : null}
        {onLocalGenerate ? (
          <button
            data-testid="editor-local-generate"
            onClick={() =>
              void onLocalGenerate({
                baseImageBlob: new Blob(["base"], { type: "image/png" }),
                editNote: "make mouth smaller",
                maskImageBlob: new Blob(["mask"], { type: "image/png" }),
                recipe: payload.recipe,
                selectedReferenceKeys: ["front:references/upload-1/front.webp"],
                uploadedReferences: [],
              })
            }
            type="button"
          >
            local generate
          </button>
        ) : null}
      </section>
    );
  },
}));

const analyzeReferenceDetailsMock = vi.mocked(analyzeReferenceDetails);
const createGenerationJobMock = vi.mocked(createGenerationJob);
const createLocalRevisionJobMock = vi.mocked(createLocalRevisionJob);
const getGenerationEventsMock = vi.mocked(getGenerationEvents);
const getGenerationJobMock = vi.mocked(getGenerationJob);
const getRequirementOptionsMock = vi.mocked(getRequirementOptions);
const uploadReferenceFileMock = vi.mocked(uploadReferenceFile);

const originalCreateObjectURL = URL.createObjectURL;
const originalRevokeObjectURL = URL.revokeObjectURL;
const WORKFLOW_STORAGE_KEY = "kig-preview.workflow.v2";

function renderWorkflow({ mobile = false }: { mobile?: boolean } = {}) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: mobile && query.includes("max-width"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  const client = new QueryClient();
  return render(
    <ThemeProvider theme={kigTheme}>
      <QueryClientProvider client={client}>
        <WorkflowPage />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("WorkflowPage", () => {
  beforeEach(async () => {
    window.localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    getRequirementOptionsMock.mockResolvedValue([]);
    uploadReferenceFileMock.mockResolvedValue({
      objectKey: "references/upload-1/front.webp",
      fileName: "front.webp",
    });
    analyzeReferenceDetailsMock.mockResolvedValue(makeDetailAnalysis());
  });

  it("uses the annotated editor export as the primary front reference when annotations exist", async () => {
    const editedImageBlob = new Blob(["edited"], { type: "image/png" });
    const annotatedImageBlob = new Blob(["annotated"], { type: "image/png" });

    const slots = createEditorReferenceSlots(
      {
        annotatedImageBlob,
        annotationPrompt: "标注 1: keep the eyes",
        editedImageBlob,
        recipe: { annotations: [{ note: "keep the eyes" }], face: {}, eyes: {}, liquify: [] } as never,
      },
      "front-revision",
    );

    expect(slots[0]).toEqual(expect.objectContaining({ kind: "front" }));
    await expect(slots[0].file?.text()).resolves.toBe("annotated");
  });

  afterEach(async () => {
    cleanup();
    window.localStorage.clear();
    await i18n.changeLanguage(DEFAULT_LOCALE);
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    restoreUrlFunction("createObjectURL", originalCreateObjectURL);
    restoreUrlFunction("revokeObjectURL", originalRevokeObjectURL);
  });

  it("renders the four required workflow steps", () => {
    renderWorkflow();
    expect(screen.getAllByRole("tab")).toHaveLength(4);
  });

  it("does not expose the rejected prompt composer label", () => {
    renderWorkflow();
    expect(screen.queryByText("Prompt 积木")).toBeNull();
  });

  it("renders the KigCraft brand and clickable implemented header destinations", () => {
    renderWorkflow();

    expect(screen.getByAltText("KigCraft")).toBeInTheDocument();
    expect(screen.getByText(/KigCraft/)).toBeInTheDocument();
    expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole("button", { name: "编辑器" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "任务记录" })).toBeInTheDocument();
    expect(screen.queryByText("棰濆害")).toBeNull();
    expect(screen.queryByText("审计面板")).toBeNull();
  });

  it("collapses implemented header destinations into a mobile menu", () => {
    renderWorkflow({ mobile: true });

    expect(screen.getByTestId("header-nav-menu")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "閹存垹娈戦惄绋垮斀" })).toBeNull();

    fireEvent.click(screen.getByTestId("header-nav-menu"));

    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("keeps desktop language and GitHub actions aligned after about", () => {
    renderWorkflow();

    const aboutButton = screen.getByTestId("header-nav-about");
    const languageButton = screen.getByTestId("header-language-button");
    const githubButton = screen.getByTestId("header-github-link");

    expect(Boolean(aboutButton.compareDocumentPosition(languageButton) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(Boolean(languageButton.compareDocumentPosition(githubButton) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(languageButton).toHaveStyle({ height: "40px" });
    expect(githubButton).toHaveStyle({ height: "40px", width: "40px" });
  });

  it("uses the full content width instead of a persistent right sidebar", () => {
    renderWorkflow();

    expect(screen.queryByTestId("workflow-right-sidebar")).toBeNull();
    expect(screen.queryByText("褰撳墠鐒︾偣")).toBeNull();
    expect(screen.queryByTestId("workflow-task-status")).toBeNull();
  });

  it("opens the front editor step directly without the upload main content", () => {
    renderWorkflow();
    uploadReference();

    fireEvent.click(screen.getAllByRole("tab")[2]);

    expect(screen.queryByTestId("workflow-task-status")).toBeNull();
    expect(screen.getByTestId("workflow-front-editor-area")).toBeInTheDocument();
    expect(screen.getByTestId("direct-refine-file-input")).toBeInTheDocument();
    expect(screen.queryByTestId("generate-front-button")).toBeNull();
  });

  it("opens the edit step without generation and uploads a local image for refinement", async () => {
    const { createObjectURL } = stubObjectUrls();
    renderWorkflow();

    fireEvent.click(screen.getByRole("button", { name: "编辑器" }));

    expect(screen.getByText("本地图片编辑器")).toBeInTheDocument();

    const file = new File(["direct-edit"], "local-result.webp", { type: "image/webp" });
    fireEvent.change(screen.getByTestId("direct-refine-file-input"), { target: { files: [file] } });

    expect(createObjectURL).toHaveBeenCalledWith(file);
    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();
    expect(screen.getByTestId("editor-image-url")).toHaveTextContent("blob:local-result.webp:1");
  });

  it("clears a direct refinement image and returns to the local upload entry", async () => {
    const { revokeObjectURL } = stubObjectUrls();
    renderWorkflow();

    fireEvent.click(screen.getByRole("button", { name: "编辑器" }));
    const file = new File(["direct-edit"], "local-result.webp", { type: "image/webp" });
    fireEvent.change(screen.getByTestId("direct-refine-file-input"), { target: { files: [file] } });
    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "娓呯┖鍥剧墖" }));

    expect(screen.queryByRole("heading", { name: "Editor candidate 1" })).toBeNull();
    expect(screen.getByText("本地图片编辑器")).toBeInTheDocument();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:local-result.webp:1");
  });

  it("allows opening the front editor step directly and uploading a local image", async () => {
    const { createObjectURL } = stubObjectUrls();
    renderWorkflow();

    const tabs = screen.getAllByRole("tab");
    expect(tabs[2]).toBeEnabled();
    fireEvent.click(tabs[2]);

    expect(screen.getByTestId("workflow-front-editor-area")).toBeInTheDocument();

    const file = new File(["local-front"], "local-front.webp", { type: "image/webp" });
    fireEvent.change(screen.getByTestId("direct-refine-file-input"), { target: { files: [file] } });

    expect(createObjectURL).toHaveBeenCalledWith(file);
    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();
    expect(screen.getByTestId("editor-image-url")).toHaveTextContent("blob:local-front.webp:1");
    expect(screen.getByTestId("editor-tools")).toHaveTextContent("all");
    expect(screen.getByTestId("editor-regenerate")).toBeInTheDocument();
    expect(screen.getByTestId("editor-secondary-regenerate")).toBeInTheDocument();
    expect(screen.queryByTestId("editor-local-generate")).toBeNull();
  });

  it("allows opening the turnaround editor step directly with annotation-only local upload", async () => {
    const { createObjectURL } = stubObjectUrls();
    renderWorkflow();

    const tabs = screen.getAllByRole("tab");
    expect(tabs[3]).toBeEnabled();
    fireEvent.click(tabs[3]);

    expect(screen.getByTestId("workflow-turnaround-editor-area")).toBeInTheDocument();

    const file = new File(["local-turnaround"], "local-turnaround.webp", { type: "image/webp" });
    fireEvent.change(screen.getByTestId("direct-refine-file-input"), { target: { files: [file] } });

    expect(createObjectURL).toHaveBeenCalledWith(file);
    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();
    expect(screen.getByTestId("editor-image-url")).toHaveTextContent("blob:local-turnaround.webp:1");
    expect(screen.getByTestId("editor-tools")).toHaveTextContent("annotation");
    expect(screen.getByTestId("editor-regenerate")).toBeInTheDocument();
    expect(screen.queryByTestId("editor-secondary-regenerate")).toBeNull();
  });

  it("enables generation step navigation after uploading a reference", () => {
    renderWorkflow();

    const generationTab = screen.getAllByRole("tab")[2];
    expect(generationTab).toBeEnabled();

    uploadReference();

    expect(generationTab).toBeEnabled();
    fireEvent.click(generationTab);
    expect(screen.queryByTestId("workflow-task-status")).toBeNull();
    expect(screen.getByTestId("workflow-front-editor-area")).toBeInTheDocument();
    expect(screen.queryByTestId("generate-front-button")).toBeNull();
  });

  it("requires a front reference before enabling next or generate", async () => {
    stubObjectUrls();
    renderWorkflow();

    const generationTab = screen.getAllByRole("tab")[1];
    const generateButton = getGenerateButton();
    expect(generationTab).toBeEnabled();
    expect(generateButton).toBeDisabled();

    await uploadSlotReference("侧面文件", "side.webp");

    expect(generationTab).toBeEnabled();
    expect(generateButton).toBeDisabled();

    await uploadSlotReference("正脸参考文件", "front.webp");

    expect(generationTab).toBeEnabled();
    expect(generateButton).toBeEnabled();
  });

  it("keeps a reference preview after workflow state updates", async () => {
    const { createObjectURL } = stubObjectUrls();
    renderWorkflow();

    await uploadSlotReference("正脸参考文件", "front.webp");

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("reference-preview-front")).toHaveAttribute("src", "blob:front.webp:1");
  });

  it("revokes the previous preview URL when replacing a reference", async () => {
    const { revokeObjectURL } = stubObjectUrls();
    renderWorkflow();

    await uploadSlotReference("正脸参考文件", "front.webp");
    await uploadSlotReference("正脸参考文件", "front-replacement.webp");

    expect(revokeObjectURL).toHaveBeenCalledWith("blob:front.webp:1");
    expect(screen.getByTestId("reference-preview-front")).toHaveAttribute(
      "src",
      "blob:front-replacement.webp:2",
    );
  });

  it("treats succeeded, failed, and cancelled generation statuses as terminal", () => {
    expect(isTerminalGenerationStatus("succeeded")).toBe(true);
    expect(isTerminalGenerationStatus("failed")).toBe(true);
    expect(isTerminalGenerationStatus("cancelled")).toBe(true);
    expect(isTerminalGenerationStatus("running")).toBe(false);
    expect(isTerminalGenerationStatus(undefined)).toBe(false);
  });

  it("labels the first step action as detail confirmation", () => {
    renderWorkflow();

    expect(getGenerateButton()).toHaveTextContent("确认细节");
    expect(getDirectGenerateButton()).toHaveTextContent("直接生成");
  });

  it("opens the detail confirmation step while analysis is pending", async () => {
    stubObjectUrls();
    const detailRequest = deferred<DetailAnalysis>();
    analyzeReferenceDetailsMock.mockReturnValueOnce(detailRequest.promise);
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    await waitFor(() => expect(screen.getAllByRole("tab")[1]).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("heading", { name: "确认细节" })).toBeInTheDocument();
    expect(screen.getByText("分析细节中")).toBeInTheDocument();
    expect(screen.getByTestId("detail-analysis-elapsed")).toHaveTextContent("已用时间");
    expect(screen.getByTestId("detail-analysis-eta")).toHaveTextContent("预计剩余");
    expect(createGenerationJobMock).not.toHaveBeenCalled();

    await act(async () => {
      detailRequest.resolve(makeDetailAnalysis());
      await detailRequest.promise;
    });
  });

  it("passes the selected locale to detail analysis", async () => {
    stubObjectUrls();
    renderWorkflow();

    fireEvent.click(screen.getByRole("button", { name: "语言 中文" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "日本語" }));
    await screen.findByRole("button", { name: "语言 日本語" });

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    await waitFor(() => expect(analyzeReferenceDetailsMock).toHaveBeenCalledTimes(1));
    expect(analyzeReferenceDetailsMock).toHaveBeenCalledWith(expect.objectContaining({ locale: "ja" }));
  });

  it("analyzes uploaded front reference before creating a generation job", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    await waitFor(() => expect(uploadReferenceFileMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(analyzeReferenceDetailsMock).toHaveBeenCalledTimes(1));
    expect(createGenerationJobMock).not.toHaveBeenCalled();
    expect(analyzeReferenceDetailsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        characterSessionId: null,
        freeText: "",
        requirementIds: [],
        referenceKeys: ["front:references/upload-1/front.webp"],
      }),
    );
    expect(await screen.findByDisplayValue("Left black X hair clip")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Right black X hair clip")).toBeInTheDocument();

    await confirmFrontGeneration();
    expect(createGenerationJobMock).toHaveBeenCalledWith(expect.objectContaining({ locale: "zh-CN" }));
  });

  it("shows real provider errors when detail analysis fails", async () => {
    stubObjectUrls();
    analyzeReferenceDetailsMock.mockRejectedValueOnce(
      new ApiError("detail analysis failed", 502, "detail_analysis_provider_failed: upstream credentials expired"),
    );
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("生成服务 detail analysis 失败：upstream credentials expired");
    expect(createGenerationJobMock).not.toHaveBeenCalled();
  });

  it("can skip detail confirmation and create a front generation job directly", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getDirectGenerateButton()).toBeEnabled());
    fireEvent.click(getDirectGenerateButton());

    await waitFor(() => expect(uploadReferenceFileMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createGenerationJobMock).toHaveBeenCalledTimes(1));
    expect(analyzeReferenceDetailsMock).not.toHaveBeenCalled();
    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        detailLock: null,
        generationMode: "front_design",
        referenceKeys: ["front:references/upload-1/front.webp"],
      }),
    );
    expect(screen.getAllByRole("tab")[2]).toHaveAttribute("aria-selected", "true");
  });

  it("ignores stale detail analysis after the front reference changes", async () => {
    stubObjectUrls();
    const detailRequest = deferred<DetailAnalysis>();
    uploadReferenceFileMock.mockImplementation(async (kind, file) => ({
      objectKey: `references/upload-1/${kind}-${file.name}`,
      fileName: file.name,
    }));
    analyzeReferenceDetailsMock.mockReturnValueOnce(detailRequest.promise);
    renderWorkflow();

    await uploadSlotReference("front", "front-a.webp");
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    await waitFor(() => expect(analyzeReferenceDetailsMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getAllByRole("tab")[0]);
    await uploadSlotReference("front", "front-b.webp");

    await act(async () => {
      detailRequest.resolve(makeDetailAnalysis());
      await detailRequest.promise;
    });

    expect(screen.queryByDisplayValue("Left black X hair clip")).not.toBeInTheDocument();
    expect(screen.getByTestId("reference-preview-front")).toHaveAttribute("src", "blob:front-b.webp:2");
  });

  it("ignores stale detail analysis after free text changes", async () => {
    stubObjectUrls();
    const detailRequest = deferred<DetailAnalysis>();
    analyzeReferenceDetailsMock.mockReturnValueOnce(detailRequest.promise);
    renderWorkflow();

    await uploadSlotReference("front", "front.webp");
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    fireEvent.click(getGenerateButton());

    await waitFor(() => expect(analyzeReferenceDetailsMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getAllByRole("tab")[0]);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "keep the bow smaller" },
    });

    await act(async () => {
      detailRequest.resolve(makeDetailAnalysis());
      await detailRequest.promise;
    });

    expect(screen.queryByDisplayValue("Left black X hair clip")).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab")[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByRole("tab")[1]).toHaveAttribute("aria-selected", "false");
  });

  it("clears confirmed details after replacing the analyzed front reference", async () => {
    stubObjectUrls();
    renderWorkflow();

    await uploadSlotReference("front", "front-a.webp");
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    expect(await screen.findByDisplayValue("Left black X hair clip")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("tab")[0]);
    await uploadSlotReference("front", "front-b.webp");
    expect(screen.getByTestId("reference-preview-front")).toHaveAttribute("src", "blob:front-b.webp:2");
    fireEvent.click(screen.getAllByRole("tab")[1]);

    expect(screen.queryByDisplayValue("Left black X hair clip")).not.toBeInTheDocument();
  });

  it("submits edited detail lock and crop references when confirmed", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    renderWorkflow();

    uploadReference();
    await analyzeUploadedReference();

    fireEvent.change(screen.getByDisplayValue("Left black X hair clip"), {
      target: { value: "Edited left black X hair clip" },
    });
    await confirmFrontGeneration();

    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        detailLock: expect.objectContaining({
          sourceAnalysisId: "analysis-a",
          features: expect.arrayContaining([
            expect.objectContaining({
              description: "Edited left black X hair clip",
              kind: "headwear",
            }),
          ]),
          crops: [
            {
              referenceKey: "detail:references/analysis-a/detail-1.webp",
              kind: "headwear",
              description: "Black X hair clip close-up",
            },
          ],
        }),
        referenceDescriptions: expect.arrayContaining([
          {
            referenceKey: "detail:references/analysis-a/detail-1.webp",
            description: "Black X hair clip close-up",
          },
        ]),
        referenceKeys: [
          "front:references/upload-1/front.webp",
          "detail:references/analysis-a/detail-1.webp",
        ],
      }),
    );
  });

  it("uploads a manual detail image and submits it as a detail lock reference", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    uploadReferenceFileMock
      .mockResolvedValueOnce({ objectKey: "references/upload-1/front.webp", fileName: "front.webp" })
      .mockResolvedValueOnce({ objectKey: "references/manual/sideburn.webp", fileName: "sideburn.webp" });
    renderWorkflow();

    uploadReference();
    await analyzeUploadedReference();
    fireEvent.click(screen.getByRole("button", { name: "新增细节图片" }));
    fireEvent.change(screen.getByLabelText("细节说明", { selector: "textarea" }), {
      target: { value: "Left sideburn curve" },
    });
    const file = new File(["manual image"], "sideburn.webp", { type: "image/webp" });
    fireEvent.change(screen.getByLabelText("选择图片"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存图片" }));

    await waitFor(() => expect(uploadReferenceFileMock).toHaveBeenCalledTimes(2));
    await confirmFrontGeneration();

    expect(uploadReferenceFileMock).toHaveBeenNthCalledWith(2, "detail", file);
    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        detailLock: expect.objectContaining({
          crops: expect.arrayContaining([
            {
              referenceKey: "detail:references/manual/sideburn.webp",
              kind: "hair",
              description: "Left sideburn curve",
            },
          ]),
        }),
        referenceDescriptions: expect.arrayContaining([
          {
            referenceKey: "detail:references/manual/sideburn.webp",
            description: "Left sideburn curve",
          },
        ]),
        referenceKeys: expect.arrayContaining(["detail:references/manual/sideburn.webp"]),
      }),
    );
  });

  it("restores analyzed detail state with reference slots and submits base plus crop references", async () => {
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    window.localStorage.setItem(
      WORKFLOW_STORAGE_KEY,
      JSON.stringify({
        step: 2,
        characterSessionId: "session-restored",
        activeJobId: null,
        selectedCandidateIndex: null,
        selectedRequirementIds: [],
        freeText: "keep the original color balance",
        referenceSlots: [
          {
            kind: "front",
            label: "Front",
            required: true,
            fileName: "front-restored.webp",
            objectKey: "references/upload-restored/front.webp",
            description: "Main restored front reference",
          },
        ],
        detailConfirmation: makeDetailAnalysisForPersistence(),
      }),
    );

    renderWorkflow();

    expect(await screen.findByDisplayValue("Left black X hair clip")).toBeInTheDocument();
    const generateButton = screen.getByRole("button", { name: /生成正视图|鐢熸垚姝/ });
    expect(generateButton).toBeEnabled();
    fireEvent.click(generateButton);

    await waitFor(() => expect(createGenerationJobMock).toHaveBeenCalledTimes(1));
    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        characterSessionId: "session-restored",
        freeText: "keep the original color balance",
        referenceKeys: [
          "front:references/upload-restored/front.webp",
          "detail:references/analysis-a/detail-1.webp",
        ],
      }),
    );
  });

  it("keeps reanalysis disabled while a generation request is pending", async () => {
    stubObjectUrls();
    const pendingGeneration = deferred<GenerationJob>();
    createGenerationJobMock.mockReturnValueOnce(pendingGeneration.promise);
    getGenerationEventsMock.mockResolvedValue([]);
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();

    fireEvent.click(screen.getAllByRole("tab")[1]);
    const reanalyzeButton = screen.getByRole("button", { name: /重新分析/ });
    expect(reanalyzeButton).toBeDisabled();
    fireEvent.click(reanalyzeButton);
    expect(analyzeReferenceDetailsMock).toHaveBeenCalledTimes(1);

    const resolvedJob = makeGenerationJob({ outputIndexes: [1] });
    getGenerationJobMock.mockResolvedValue(resolvedJob);
    pendingGeneration.resolve(resolvedJob);

    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();
    expect(createGenerationJobMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the newest crop replacement when an older upload resolves later", async () => {
    const firstUpload = deferred<{ objectKey: string; fileName: string }>();
    const secondUpload = deferred<{ objectKey: string; fileName: string }>();
    uploadReferenceFileMock
      .mockResolvedValueOnce({ objectKey: "references/upload-1/front.webp", fileName: "front.webp" })
      .mockReturnValueOnce(firstUpload.promise)
      .mockReturnValueOnce(secondUpload.promise);
    stubObjectUrls();
    renderWorkflow();

    uploadReference();
    await analyzeUploadedReference();

    const replaceInput = screen.getByLabelText("替换 Black X hair clip close-up");
    fireEvent.change(replaceInput, {
      target: { files: [new File(["first"], "crop-first.webp", { type: "image/webp" })] },
    });
    fireEvent.change(replaceInput, {
      target: { files: [new File(["second"], "crop-second.webp", { type: "image/webp" })] },
    });

    secondUpload.resolve({ objectKey: "references/analysis-a/detail-new.webp", fileName: "crop-second.webp" });
    await waitFor(() =>
      expect(screen.getByAltText("Black X hair clip close-up")).toHaveAttribute(
        "src",
        "/api/references/references/analysis-a/detail-new.webp",
      ),
    );

    firstUpload.resolve({ objectKey: "references/analysis-a/detail-old.webp", fileName: "crop-first.webp" });
    await Promise.resolve();

    expect(screen.getByAltText("Black X hair clip close-up")).toHaveAttribute(
      "src",
      "/api/references/references/analysis-a/detail-new.webp",
    );
  });

  it("allows generation retry after a failed job", async () => {
    stubObjectUrls();
    getRequirementOptionsMock.mockResolvedValue([
      {
        id: "requirement-soft-smile",
        group: "expression",
        label: "Soft smile",
        description: "Use a soft smile",
        prompt_text: "soft smile",
        sort_order: 1,
      },
    ]);
    mockGenerationApi(makeGenerationJob({ status: "failed", outputIndexes: [] }));
    renderWorkflow();

    uploadReference();
    fireEvent.click(await screen.findByRole("button", { name: "Soft smile" }));
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();
    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        characterSessionId: null,
        requirementIds: ["requirement-soft-smile"],
        referenceKeys: expect.arrayContaining(["front:references/upload-1/front.webp"]),
      }),
    );
    expect(createGenerationJobMock).not.toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: expect.any(String),
        chipIds: expect.any(Array),
      }),
    );

    expect(await screen.findByTestId("generation-status")).toHaveTextContent(/失败|failed/i);
    fireEvent.click(screen.getAllByRole("tab")[0]);
    expect(getGenerateButton()).toBeEnabled();
  });

  it("hides generation helper copy and backend provider status text", async () => {
    stubObjectUrls();
    mockGenerationApi(
      makeGenerationJob({
        outputIndexes: [],
        phaseLabel: "Codex generating",
        progress: 45,
        status: "codex_generating",
      }),
      [
        {
          sequence: 1,
          type: "codex_generating",
          progress: 45,
          message: "Codex generating",
          created_at: "2026-06-23T12:00:00Z",
        },
      ],
    );
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();
    expect(screen.queryByText("鐢熸垚鎺у埗")).toBeNull();
    expect(await screen.findByTestId("generation-status")).toHaveTextContent(/.+/);
    expect(screen.queryByText(/codex/i)).toBeNull();
  });

  it("uploads a local front reference before creating a generation job", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [] }));
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();

    await waitFor(() => expect(uploadReferenceFileMock).toHaveBeenCalledTimes(1));
    await confirmFrontGeneration();

    expect(uploadReferenceFileMock).toHaveBeenCalledWith(
      "front",
      expect.objectContaining({ name: "front.webp" }),
    );
    expect(createGenerationJobMock).toHaveBeenCalledWith(
      expect.objectContaining({
        referenceKeys: expect.arrayContaining(["front:references/upload-1/front.webp"]),
      }),
    );
  });

  it("reuses the returned character session id on subsequent generation", async () => {
    stubObjectUrls();
    mockGenerationApi(
      makeGenerationJob({ sessionId: "session-existing", outputIndexes: [1] }),
      [],
      makeGenerationJob({ id: "job-next", sessionId: "session-existing", outputIndexes: [1] }),
    );
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();
    expect(await screen.findByRole("heading", { level: 2, name: /1/ })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("tab")[0]);
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());

    await analyzeUploadedReference();
    await confirmFrontGeneration();
    await waitFor(() => expect(createGenerationJobMock).toHaveBeenCalledTimes(2));

    expect(createGenerationJobMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        characterSessionId: "session-existing",
        requirementIds: [],
        referenceKeys: expect.arrayContaining(["front:references/upload-1/front.webp"]),
      }),
    );
  });

  it("passes the backend one-based candidate index into the editor", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [4] }));
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();

    expect(await screen.findByRole("heading", { level: 2, name: /4/ })).toBeInTheDocument();
    expect(screen.getByTestId("editor-image-url")).toHaveTextContent("/fixtures/generated/candidate-4.webp");
  });

  it("shows turnaround generation progress instead of reusing the front image while four-view output is pending", async () => {
    stubObjectUrls();
    const frontJob = makeGenerationJob({
      generationMode: "front_design",
      id: "front-job",
      outputIndexes: [1],
    });
    const turnaroundJob = makeGenerationJob({
      generationMode: "turnaround",
      id: "turnaround-job",
      outputIndexes: [],
      phaseLabel: "generating",
      progress: 45,
      status: "running",
    });

    createGenerationJobMock.mockResolvedValueOnce(frontJob).mockResolvedValueOnce(turnaroundJob);
    getGenerationJobMock.mockImplementation(async (jobId) => (jobId === "turnaround-job" ? turnaroundJob : frontJob));
    getGenerationEventsMock.mockResolvedValue([]);
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();
    expect(await screen.findByRole("heading", { name: "Editor candidate 1" })).toBeInTheDocument();
    expect(screen.getByTestId("editor-image-url")).toHaveTextContent("/fixtures/generated/candidate-1.webp");

    fireEvent.click(screen.getByTestId("editor-secondary-regenerate"));

    await waitFor(() => expect(createGenerationJobMock).toHaveBeenCalledTimes(2));
    expect(createGenerationJobMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        freeText: "嘴型更委屈，眼神更柔和",
        generationMode: "turnaround",
      }),
    );
    expect(screen.getByTestId("workflow-turnaround-editor-area")).toBeInTheDocument();
    expect(await screen.findByTestId("generation-status")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Editor candidate 1" })).toBeNull();
    expect(screen.queryByTestId("editor-image-url")).toBeNull();
  });

  it("clears a selected generated candidate and returns to the local upload entry", async () => {
    stubObjectUrls();
    mockGenerationApi(makeGenerationJob({ outputIndexes: [4] }));
    renderWorkflow();

    uploadReference();
    await waitFor(() => expect(getGenerateButton()).toBeEnabled());
    await analyzeUploadedReference();
    await confirmFrontGeneration();
    expect(await screen.findByRole("heading", { level: 2, name: /4/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "娓呯┖鍥剧墖" }));

    expect(screen.queryByRole("heading", { level: 2, name: /4/ })).toBeNull();
    expect(screen.getByTestId("generation-status")).toBeInTheDocument();
  });

  it("restores an active job and selected candidate from persisted workflow state", async () => {
    window.localStorage.setItem(
      WORKFLOW_STORAGE_KEY,
      JSON.stringify({
        step: 3,
        characterSessionId: "session-restored",
        activeJobId: "job-restored",
        selectedCandidateIndex: 2,
        selectedRequirementIds: ["more_youthful"],
        freeText: "keep the bangs",
      }),
    );
    getGenerationJobMock.mockResolvedValue(
      makeGenerationJob({ id: "job-restored", sessionId: "session-restored", outputIndexes: [2] }),
    );
    getGenerationEventsMock.mockResolvedValue([]);

    renderWorkflow();

    expect(await screen.findByRole("heading", { name: "Editor candidate 2" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("editor-image-url")).toHaveTextContent("/fixtures/generated/candidate-2.webp"),
    );
    expect(screen.getByTestId("workflow-front-editor-area")).toBeInTheDocument();
    expect(getGenerationJobMock).toHaveBeenCalledWith("job-restored");
  });
});

function restoreUrlFunction(
  name: "createObjectURL" | "revokeObjectURL",
  value: (typeof URL)["createObjectURL"] | (typeof URL)["revokeObjectURL"] | undefined,
) {
  if (value) {
    Object.defineProperty(URL, name, { configurable: true, value });
    return;
  }

  Reflect.deleteProperty(URL, name);
}

function stubObjectUrls() {
  let objectUrlIndex = 0;
  const createObjectURL = vi.fn((file: Blob) => {
    objectUrlIndex += 1;
    const fileName = file instanceof File ? file.name : `preview-${objectUrlIndex}`;
    return `blob:${fileName}:${objectUrlIndex}`;
  });
  const revokeObjectURL = vi.fn();

  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });

  return { createObjectURL, revokeObjectURL };
}

function uploadReference() {
  const input = screen.getByTestId("reference-slot-front").querySelector("input");
  if (!input) throw new Error("front reference input not found");
  const file = new File(["reference"], "front.webp", { type: "image/webp" });

  fireEvent.change(input, { target: { files: [file] } });
}

async function uploadSlotReference(label: string, name: string) {
  const input =
    screen.queryByLabelText(label) ??
    screen
      .getByTestId(/side|侧|渚/.test(label) ? "reference-slot-side" : "reference-slot-front")
      .querySelector("input");
  if (!input) throw new Error(`reference input not found for ${label}`);
  const file = new File(["reference"], name, { type: "image/webp" });

  fireEvent.change(input, { target: { files: [file] } });
  if (typeof URL.createObjectURL === "function") {
    await waitFor(() => expect(screen.getAllByTitle(new RegExp(name)).length).toBeGreaterThan(0));
    return;
  }
  await Promise.resolve();
}

function getGenerateButton() {
  return screen.getByTestId("generate-front-button");
}

function getDirectGenerateButton() {
  return screen.getByTestId("direct-generate-front-button");
}

async function analyzeUploadedReference() {
  fireEvent.click(getGenerateButton());
  await screen.findByDisplayValue("Left black X hair clip");
}

async function confirmFrontGeneration() {
  const button =
    screen.queryByRole("button", { name: "生成正视图" }) ??
    screen.getByRole("button", { name: "鐢熸垚姝ｈ鍥?" });
  fireEvent.click(button);
  await waitFor(() => expect(createGenerationJobMock).toHaveBeenCalled());
}

function mockGenerationApi(job: GenerationJob, events: JobEvent[] = [], nextJob?: GenerationJob) {
  if (nextJob) {
    createGenerationJobMock.mockResolvedValueOnce(job).mockResolvedValueOnce(nextJob);
  } else {
    createGenerationJobMock.mockResolvedValue(job);
  }
  getGenerationJobMock.mockResolvedValue(job);
  getGenerationEventsMock.mockResolvedValue(events);
}

function makeDetailAnalysis(): DetailAnalysis {
  return {
    analysisId: "analysis-a",
    features: [
      {
        id: "feature-left-hair-clip",
        kind: "headwear",
        label: "Left hair clip",
        description: "Left black X hair clip",
        confidence: 0.9,
      },
      {
        id: "feature-right-hair-clip",
        kind: "headwear",
        label: "Right hair clip",
        description: "Right black X hair clip",
        confidence: 0.88,
      },
    ],
    crops: [
      {
        id: "crop-hair-clip",
        kind: "headwear",
        description: "Black X hair clip close-up",
        sourceReferenceKey: "front:references/upload-1/front.webp",
        bbox: { x: 10, y: 20, width: 80, height: 60 },
        objectKey: "references/analysis-a/detail-1.webp",
        imageUrl: "/api/references/references/analysis-a/detail-1.webp",
      },
    ],
    warnings: [],
  };
}

function makeDetailAnalysisForPersistence() {
  return {
    analysisId: "analysis-a",
    features: [
      {
        id: "feature-left-hair-clip",
        kind: "headwear",
        label: "Left hair clip",
        description: "Left black X hair clip",
        confidence: 0.9,
      },
    ],
    crops: [
      {
        id: "crop-hair-clip",
        kind: "headwear",
        description: "Black X hair clip close-up",
        sourceReferenceKey: "front:references/upload-restored/front.webp",
        bbox: { x: 10, y: 20, width: 80, height: 60 },
        objectKey: "references/analysis-a/detail-1.webp",
        imageUrl: "/api/references/references/analysis-a/detail-1.webp",
      },
    ],
    warnings: [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

function makeGenerationJob({
  id = "job-123456",
  sessionId = "session-1",
  status = "succeeded",
  phaseLabel = status,
  progress = status === "succeeded" ? 100 : 0,
  queuePosition = null,
  outputIndexes = [1, 2, 3, 4],
  generationMode = "front_design",
}: {
  id?: string;
  sessionId?: string;
  status?: string;
  phaseLabel?: string;
  progress?: number;
  queuePosition?: number | null;
  outputIndexes?: number[];
  generationMode?: GenerationMode;
} = {}): GenerationJob {
  return {
    id,
    character_session_id: sessionId,
    generation_mode: generationMode,
    expected_output_count: 1,
    status,
    progress,
    queue_position: queuePosition,
    phase_label: phaseLabel,
    provider: "fixture",
    accepted_output_index: null,
    outputs: outputIndexes.map((index) => ({
      index,
      object_key: `candidate-${index}.webp`,
      image_url: `/fixtures/generated/candidate-${index}.webp`,
      width: 1024,
      height: 1024,
    })),
  };
}
