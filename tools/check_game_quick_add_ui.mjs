// Compiled game form against real saves; synthetic provider/artwork failures.
import assert from "node:assert/strict";
import path from "node:path";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXYAAAAASUVORK5CYII=", "base64");

export async function checkGameArtworkRetry({ member, origin, report }) {
  const page = await member.newPage();
  let creates = 0, attempts = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/game/create")) creates++;
  });
  await page.route("**/api/game/*/assets/key_art", async route => {
    attempts++;
    if (attempts === 1) await route.fulfill({ status: 503, body: "Synthetic artwork outage" });
    else await route.continue();
  });
  try {
    await page.goto(origin + "/games");
    await page.getByRole("button", { name: "+ Add Game", exact: true }).first().click();
    const quick = page.getByRole("button", { name: "Full form", exact: true });
    if (await quick.count()) await quick.click();
    const dialog = page.getByRole("dialog", { name: "Add Game", exact: true });
    await dialog.getByRole("button", { name: "General", exact: true }).click();
    await dialog.getByLabel("Title", { exact: true }).fill("Artwork retry review");
    await dialog.getByLabel("Folder name", { exact: true }).fill("artwork-retry-" + Date.now());
    await dialog.getByRole("button", { name: "Media", exact: true }).click();
    await dialog.locator('input[type="file"]').first().setInputFiles({ name: "review.png", mimeType: "image/png", buffer: png });
    await dialog.getByRole("button", { name: "Page", exact: true }).click();
    await dialog.getByRole("button", { name: "Add Game", exact: true }).click();
    await dialog.waitFor({ state: "hidden", timeout: 5000 });
    assert.equal(creates, 1, "Artwork failure does not keep a create form open");
    await page.getByText(/Game saved\. Artwork/).waitFor();
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.getByText("Artwork saved.", { exact: true }).waitFor();
    assert.equal(creates, 1, "Retry transfers artwork to the existing game only");
    assert.equal(attempts, 2);
    report.passed.push({ artwork_failure_preserves_game: true, retry_creates: 0 });
  } finally {
    await page.close();
  }
}
