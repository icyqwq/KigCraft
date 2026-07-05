import { ThemeProvider } from "@mui/material/styles";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { kigTheme } from "../../app/theme";
import i18n from "../../i18n";
import { DEFAULT_LOCALE } from "../../i18n/locales";
import type { DetailConfirmationPanelProps } from "./DetailConfirmationPanel";
import { DetailConfirmationPanel } from "./DetailConfirmationPanel";

const features = [
  {
    id: "feature-ribbon",
    kind: "headwear",
    label: "red ribbon",
    description: "red bow on the left side",
    confidence: 0.92,
  },
  {
    id: "feature-hat",
    kind: "headwear",
    label: "small hat",
    description: "tiny black hat above bangs",
    confidence: 0.84,
  },
  {
    id: "feature-eyes",
    kind: "eyes",
    label: "eyes",
    description: "round golden eyes",
    confidence: 0.9,
  },
] satisfies DetailConfirmationPanelProps["features"];

const crops = [
  {
    id: "crop-ribbon",
    kind: "headwear",
    description: "red ribbon",
    sourceReferenceKey: "front:reference-a.png",
    bbox: { x: 10, y: 20, width: 80, height: 60 },
    objectKey: "crop-ribbon.png",
    imageUrl: "/fixtures/details/ribbon.png",
  },
  {
    id: "crop-hat",
    kind: "headwear",
    description: "small black hat",
    sourceReferenceKey: "front:reference-a.png",
    bbox: { x: 90, y: 12, width: 70, height: 48 },
    objectKey: "crop-hat.png",
    imageUrl: "/fixtures/details/hat.png",
  },
] satisfies DetailConfirmationPanelProps["crops"];

function renderPanel(overrides: Partial<DetailConfirmationPanelProps> = {}) {
  const props: DetailConfirmationPanelProps = {
    canGenerate: true,
    crops,
    features,
    isAnalyzing: false,
    warnings: [],
    onBackToUpload: vi.fn(),
    onCropsChange: vi.fn(),
    onFeaturesChange: vi.fn(),
    onGenerateFront: vi.fn(),
    onAddCrop: vi.fn(),
    onReanalyze: vi.fn(),
    onReplaceCrop: vi.fn(),
    ...overrides,
  };

  render(
    <ThemeProvider theme={kigTheme}>
      <DetailConfirmationPanel {...props} />
    </ThemeProvider>,
  );

  return props;
}

describe("DetailConfirmationPanel", () => {
  beforeEach(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE);
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.kind.ears", "耳朵");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.kind.requirement", "要求");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.deleteCropAria", "删除 {{description}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.featureInputAria", "{{kind}}特征");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.cropInputAria", "{{kind}}切片");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.replaceCropAria", "替换 {{description}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.addFeature", "Add text item");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.addCrop", "Add detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualKindLabel", "Detail type");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualDescriptionLabel", "Detail description");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualImageLabel", "Detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.saveManualFeature", "Save text item");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.saveManualCrop", "Save detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.cancelManual", "Cancel");
  });

  afterEach(async () => {
    cleanup();
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.kind.ears", "耳朵");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.kind.requirement", "要求");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.deleteCropAria", "删除 {{description}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.featureInputAria", "{{kind}}特征");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.cropInputAria", "{{kind}}切片");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.replaceCropAria", "替换 {{description}}");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.addFeature", "Add text item");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.addCrop", "Add detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualKindLabel", "Detail type");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualDescriptionLabel", "Detail description");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.manualImageLabel", "Detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.saveManualFeature", "Save text item");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.saveManualCrop", "Save detail image");
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.cancelManual", "Cancel");
    await i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it("renders multiple same-kind headwear features and crops as editable items", () => {
    renderPanel();

    expect(screen.getByText("确认细节")).toBeInTheDocument();
    expect(screen.getAllByLabelText("头饰特征", { selector: "textarea" })).toHaveLength(2);
    expect(screen.getAllByLabelText("头饰切片", { selector: "textarea" })).toHaveLength(2);
    expect(screen.getByRole("img", { name: "red ribbon" })).toHaveAttribute(
      "src",
      "/fixtures/details/ribbon.png",
    );
    expect(screen.getByRole("img", { name: "small black hat" })).toHaveAttribute(
      "src",
      "/fixtures/details/hat.png",
    );
  });

  it("renders ears detail kind from i18n resources", () => {
    i18n.addResource(DEFAULT_LOCALE, "translation", "detailConfirmation.kind.ears", "耳部测试标签");

    renderPanel({
      features: [
        {
          id: "feature-cat-ears",
          kind: "ears",
          label: "cat ears",
          description: "white cat ears on top of the head",
          confidence: 0.92,
        },
      ],
      crops: [],
    });

    expect(screen.getByText("耳部测试标签")).toBeInTheDocument();
    expect(screen.queryByText("耳朵")).not.toBeInTheDocument();
  });

  it("renders optimized user requirement details from i18n resources", () => {
    renderPanel({
      features: [
        {
          id: "feature-user-requirement",
          kind: "requirement",
          label: "用户要求",
          description: "保留黑色 X 发夹、长直发和委屈表情。",
          confidence: null,
        },
      ],
      crops: [],
    });

    expect(screen.getByText("要求")).toBeInTheDocument();
    expect(screen.getByDisplayValue("保留黑色 X 发夹、长直发和委屈表情。")).toBeInTheDocument();
  });

  it("calls onFeaturesChange with the edited feature while leaving other features intact", () => {
    const onFeaturesChange = vi.fn();
    renderPanel({ onFeaturesChange });

    fireEvent.change(screen.getAllByLabelText("头饰特征", { selector: "textarea" })[0], {
      target: { value: "red ribbon with long tails" },
    });

    expect(onFeaturesChange).toHaveBeenCalledWith([
      { ...features[0], description: "red ribbon with long tails" },
      features[1],
      features[2],
    ]);
  });

  it("removes a text feature when its delete action is clicked", () => {
    const onFeaturesChange = vi.fn();
    i18n.addResource(
      DEFAULT_LOCALE,
      "translation",
      "detailConfirmation.deleteFeatureAria",
      "移除文字细节 {{description}}",
    );
    renderPanel({ onFeaturesChange });

    fireEvent.click(screen.getByRole("button", { name: "移除文字细节 red bow on the left side" }));

    expect(onFeaturesChange).toHaveBeenCalledWith([features[1], features[2]]);
  });

  it("calls onCropsChange with the edited crop while leaving other crops intact", () => {
    const onCropsChange = vi.fn();
    renderPanel({ onCropsChange });

    fireEvent.change(screen.getByDisplayValue("red ribbon"), {
      target: { value: "red ribbon close-up" },
    });

    expect(onCropsChange).toHaveBeenCalledWith([
      { ...crops[0], description: "red ribbon close-up" },
      crops[1],
    ]);
  });

  it("removes a crop when its delete action is clicked", () => {
    const onCropsChange = vi.fn();
    i18n.addResource(
      DEFAULT_LOCALE,
      "translation",
      "detailConfirmation.deleteCropAria",
      "移除细节切片 {{description}}",
    );
    renderPanel({ onCropsChange });

    fireEvent.click(screen.getByRole("button", { name: "移除细节切片 red ribbon" }));

    expect(onCropsChange).toHaveBeenCalledWith([crops[1]]);
  });

  it("calls onReplaceCrop with the selected local file", () => {
    const onReplaceCrop = vi.fn();
    i18n.addResource(
      DEFAULT_LOCALE,
      "translation",
      "detailConfirmation.replaceCropAria",
      "替换细节切片 {{description}}",
    );
    renderPanel({ onReplaceCrop });
    const file = new File(["new image"], "replacement.png", { type: "image/png" });

    fireEvent.change(screen.getByLabelText("替换细节切片 red ribbon"), {
      target: { files: [file] },
    });

    expect(onReplaceCrop).toHaveBeenCalledWith("crop-ribbon", file);
  });

  it("adds a manual text detail item to the feature list", () => {
    const onFeaturesChange = vi.fn();
    renderPanel({ onFeaturesChange });

    fireEvent.click(screen.getByRole("button", { name: "Add text item" }));
    fireEvent.change(screen.getByLabelText("Detail description", { selector: "textarea" }), {
      target: { value: "Bangs split into three long center strands" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save text item" }));

    expect(onFeaturesChange).toHaveBeenCalledWith([
      ...features,
      expect.objectContaining({
        description: "Bangs split into three long center strands",
        kind: "hair",
      }),
    ]);
  });

  it("passes a manual detail image to the workflow uploader", () => {
    const onAddCrop = vi.fn();
    renderPanel({ onAddCrop });
    const file = new File(["manual image"], "sideburn.webp", { type: "image/webp" });

    fireEvent.click(screen.getByRole("button", { name: "Add detail image" }));
    fireEvent.change(screen.getByLabelText("Detail description", { selector: "textarea" }), {
      target: { value: "Left sideburn curve" },
    });
    fireEvent.change(screen.getByLabelText("Detail image"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save detail image" }));

    expect(onAddCrop).toHaveBeenCalledWith({
      description: "Left sideburn curve",
      file,
      kind: "hair",
    });
  });

  it("calls top actions and disables generate while unavailable or analyzing", () => {
    const onBackToUpload = vi.fn();
    const onGenerateFront = vi.fn();
    const onReanalyze = vi.fn();
    const { rerender } = render(
      <ThemeProvider theme={kigTheme}>
        <DetailConfirmationPanel
          canGenerate={false}
          crops={crops}
          features={features}
          isAnalyzing={false}
          warnings={[]}
          onBackToUpload={onBackToUpload}
          onCropsChange={vi.fn()}
          onFeaturesChange={vi.fn()}
          onGenerateFront={onGenerateFront}
          onAddCrop={vi.fn()}
          onReanalyze={onReanalyze}
          onReplaceCrop={vi.fn()}
        />
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "返回上传" }));
    fireEvent.click(screen.getByRole("button", { name: "重新分析" }));
    expect(screen.getByRole("button", { name: "生成正视图" })).toBeDisabled();

    rerender(
      <ThemeProvider theme={kigTheme}>
        <DetailConfirmationPanel
          canGenerate={true}
          crops={crops}
          features={features}
          isAnalyzing={true}
          warnings={[]}
          onBackToUpload={onBackToUpload}
          onCropsChange={vi.fn()}
          onFeaturesChange={vi.fn()}
          onGenerateFront={onGenerateFront}
          onAddCrop={vi.fn()}
          onReanalyze={onReanalyze}
          onReplaceCrop={vi.fn()}
        />
      </ThemeProvider>,
    );

    expect(screen.getByRole("button", { name: "生成正视图" })).toBeDisabled();

    rerender(
      <ThemeProvider theme={kigTheme}>
        <DetailConfirmationPanel
          canGenerate={true}
          crops={crops}
          features={features}
          isAnalyzing={false}
          warnings={[]}
          onBackToUpload={onBackToUpload}
          onCropsChange={vi.fn()}
          onFeaturesChange={vi.fn()}
          onGenerateFront={onGenerateFront}
          onAddCrop={vi.fn()}
          onReanalyze={onReanalyze}
          onReplaceCrop={vi.fn()}
        />
      </ThemeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "生成正视图" }));

    expect(onBackToUpload).toHaveBeenCalledTimes(1);
    expect(onReanalyze).toHaveBeenCalledTimes(1);
    expect(onGenerateFront).toHaveBeenCalledTimes(1);
  });

  it("shows compact timing feedback while detail analysis is running", () => {
    renderPanel({
      analysisElapsedMs: 75_000,
      analysisEtaMs: 45_000,
      isAnalyzing: true,
    });

    expect(screen.getByText("分析细节中")).toBeInTheDocument();
    expect(screen.getByTestId("detail-analysis-elapsed")).toHaveTextContent("已用时间 1 分 15 秒");
    expect(screen.getByTestId("detail-analysis-eta")).toHaveTextContent("预计剩余 1 分钟");
    expect(screen.queryByRole("progressbar", { name: "生成进度" })).not.toBeInTheDocument();
  });
});
