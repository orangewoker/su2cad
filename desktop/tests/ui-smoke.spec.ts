import { expect, test } from "@playwright/test";

test("liquid glass shell renders without horizontal clipping", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("http://127.0.0.1:1420");
  await page.waitForTimeout(500);
  expect(errors).toEqual([]);
  await expect(page.locator(".brand")).toContainText("SU2CAD");
  await expect(page.locator(".titlebar")).toHaveAttribute("data-tauri-drag-region", "true");
  await expect(page.locator(".settings-panel")).toBeVisible();
  await expect(page.locator(".primary-action")).toContainText("生成 CAD");
});

test("plugin manager is available from the title bar", async ({ page }) => {
  await page.goto("http://127.0.0.1:1420");
  await page.getByRole("button", { name: "插件", exact: true }).click();
  await expect(page.locator(".plugin-modal")).toBeVisible();
  await expect(page.locator(".plugin-modal")).toContainText("一键安装 / 修复");
});

test("compact window keeps the primary action visible", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto("http://127.0.0.1:1420");
  await expect(page.locator(".primary-action")).toBeVisible();
  await expect(page.locator(".floating-dock")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
