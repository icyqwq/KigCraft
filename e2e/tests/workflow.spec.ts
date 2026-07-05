import { expect, test, type Locator, type Page, type Response } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const referenceImagePath = path.resolve(__dirname, "../../ref/商成品参考图.webp");

type RequirementOption = {
  id: string;
  label: string;
};

type GenerationJob = {
  id: string;
  status: string;
  outputs: Array<{
    index: number;
    image_url: string;
  }>;
};

async function readJsonResponse<T>(response: Response): Promise<T> {
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`Expected JSON response: ${error instanceof Error ? error.message : String(error)}`);
  }

  return body as T;
}

function waitForApiPost(page: Page, pathPart: string) {
  return page.waitForResponse(
    (response) =>
      response.url().includes(pathPart) &&
      response.request().method() === "POST" &&
      response.ok(),
  );
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function selectRequirement(page: Page, requirements: RequirementOption[], id: string) {
  const requirement = requirements.find((item) => item.id === id);
  if (!requirement) {
    throw new Error(`Missing requirement option ${id}`);
  }

  await page.getByRole("button", { name: new RegExp(`^${escapeRegExp(requirement.label)}$`) }).click();
}

async function readLocatorSignature(locator: Locator) {
  const image = await locator.screenshot({ animations: "disabled" });
  let nonBlankBytes = 0;
  let weightedSignature = 0;

  for (let index = 0; index < image.length; index += 64) {
    const byte = image[index] ?? 0;
    if (byte !== 0 && byte !== 255) {
      nonBlankBytes += 1;
    }
    weightedSignature = (weightedSignature + byte * 17 + index) % 1_000_000_007;
  }

  return { nonBlankBytes, weightedSignature };
}

async function expectStageSignatureToChange(stage: Locator, previousSignature: number) {
  await expect
    .poll(
      async () => {
        const next = await readLocatorSignature(stage);
        return next.weightedSignature;
      },
      { timeout: 6000 },
    )
    .not.toBe(previousSignature);
}

async function setSliderToMaximum(sliderRoot: Locator, expectedValue: string | RegExp) {
  await sliderRoot.scrollIntoViewIfNeeded();
  const thumb = sliderRoot.getByRole("slider").first();
  await thumb.focus();
  await thumb.press("End");
  await expect(thumb).toHaveAttribute("aria-valuenow", expectedValue);
}

test("runs upload, generation, editor, album, regeneration, restore, and audit flow", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.clear());
  const requirementsResponse = page.waitForResponse(
    (response) => response.url().includes("/api/prompts/requirements") && response.ok(),
  );

  await page.reload();
  const requirements = await readJsonResponse<RequirementOption[]>(await requirementsResponse);

  await expect(page.getByText("Kig Preview")).toBeVisible();
  await expect(page.getByText("上传参考图")).toBeVisible();
  await expect(page.getByText("生成候选")).toBeVisible();
  await expect(page.getByText("精修保存")).toBeVisible();

  await page.locator('input[type="file"]').first().setInputFiles(referenceImagePath);
  await expect(page.getByText("商成品参考图.webp")).toBeVisible();

  await selectRequirement(page, requirements, "more_youthful");
  await selectRequirement(page, requirements, "soft_eyes");
  await selectRequirement(page, requirements, "four_view_final");
  await page.getByLabel("补充说明").fill("保留角色识别特征，表情更柔和，输出白底支架展示的四视角成品预览。");

  const firstJobResponse = waitForApiPost(page, "/api/generation/jobs");
  await page.getByRole("button", { name: /生成 4 个方案/ }).click();
  const firstJob = await readJsonResponse<GenerationJob>(await firstJobResponse);
  const firstJobPrefix = firstJob.id.slice(0, 8);

  await expect(page.getByTestId("generation-job-id")).toContainText(firstJobPrefix);
  await expect(page.getByTestId("generation-status")).toHaveText("succeeded", { timeout: 15000 });

  for (const index of [1, 2, 3, 4]) {
    const card = page.getByTestId(`candidate-card-${index}`);
    await expect(card).toBeVisible();
    await expect(card.locator("img")).toHaveAttribute("src", new RegExp(`/api/static/fixtures/kigurumi-candidate-${index}\\.webp`));
  }

  await page.getByTestId("candidate-card-1").click();
  await expect(page.getByTestId("active-editor-tool")).toHaveText("标注");
  await page.getByTestId("annotation-note").fill("圈出需要保留的刘海方向和眼部柔和感。");

  const editorStage = page.getByTestId("editor-stage");
  const baseImage = page.getByTestId("editor-base-image");
  await expect(baseImage).toBeVisible({ timeout: 15000 });
  await expect
    .poll(async () => baseImage.evaluate((image) => (image as HTMLImageElement).naturalWidth), { timeout: 15000 })
    .toBeGreaterThan(0);
  await expect(editorStage.locator("canvas").first()).toBeVisible({ timeout: 15000 });
  const initialStage = await readLocatorSignature(editorStage);
  expect(initialStage.nonBlankBytes).toBeGreaterThan(0);

  await page.getByTestId("editor-tool-face").click();
  await expect(page.getByTestId("active-editor-tool")).toHaveText("脸型");
  const beforeFace = await readLocatorSignature(editorStage);
  await setSliderToMaximum(page.getByTestId("face-control-faceWidth"), "50");
  await expectStageSignatureToChange(editorStage, beforeFace.weightedSignature);
  const afterFace = await readLocatorSignature(editorStage);

  await page.getByTestId("editor-tool-eyes").click();
  await expect(page.getByTestId("active-editor-tool")).toHaveText("眼睛");
  const beforeEyes = await readLocatorSignature(editorStage);
  await setSliderToMaximum(page.getByTestId("eye-control-eyeHeight"), "50");
  await expectStageSignatureToChange(editorStage, beforeEyes.weightedSignature);
  const afterEyes = await readLocatorSignature(editorStage);

  await page.getByTestId("editor-tool-liquify").click();
  await expect(page.getByTestId("active-editor-tool")).toHaveText("液化");
  await setSliderToMaximum(page.getByTestId("liquify-strength-slider"), /^1(?:\.0+)?$/);
  const beforeLiquify = await readLocatorSignature(editorStage);
  await page.getByTestId("liquify-add-stroke").click();
  await expect(page.getByTestId("liquify-stroke-count")).toContainText("1");
  await expectStageSignatureToChange(editorStage, beforeLiquify.weightedSignature);

  await page.getByRole("button", { name: /保存至相册/ }).click();
  await expect(page.getByTestId("album-save-status")).toContainText("已保存到相册");
  await expect(page.getByTestId("album-saved-count")).toHaveText(/已保存 [1-9]\d* 张结果/);

  const regeneratedJobResponse = waitForApiPost(page, "/api/generation/jobs");
  await page.getByRole("button", { name: /用编辑结果重新生成/ }).click();
  const regeneratedJob = await readJsonResponse<GenerationJob>(await regeneratedJobResponse);
  expect(regeneratedJob.id).not.toBe(firstJob.id);
  const regeneratedJobPrefix = regeneratedJob.id.slice(0, 8);

  await expect(page.getByTestId("generation-job-id")).toContainText(regeneratedJobPrefix);
  await expect(page.getByTestId("generation-status")).toHaveText("succeeded", { timeout: 15000 });
  await expect(page.getByTestId("candidate-card-4")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("generation-job-id")).toContainText(regeneratedJobPrefix);
  await expect(page.getByTestId("generation-status")).toHaveText("succeeded", { timeout: 15000 });
  await expect(page.getByTestId("candidate-card-1")).toBeVisible();

  await page.goto("/admin/audit");
  await expect(page.getByRole("heading", { name: "审计面板" })).toBeVisible();
  await expect(page.getByTestId("audit-total-users")).toContainText("用户数量");
  await expect(page.getByTestId("audit-total-calls")).toContainText("调用次数");
  await expect(page.getByTestId("quota-window-hours")).toBeVisible();
  await expect(page.getByTestId("quota-normal-window-limit")).toBeVisible();
  await expect(page.getByTestId("quota-parallel-generation-limit")).toBeVisible();
});
