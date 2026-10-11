// Compiled Worlds UI against real archive APIs with disposable review records.
import assert from "node:assert/strict";
import path from "node:path";

const zip = Buffer.from("UEsDBBQAAAAAAFMqS12O1ZjsFgAAABYAAAAPAAAAd29ybGQvbGV2ZWwuZGF0c3ludGhldGljIHJldmlldyB3b3JsZFBLAQIUAxQAAAAAAFMqS12O1ZjsFgAAABYAAAAPAAAAAAAAAAAAAACAAQAAAAB3b3JsZC9sZXZlbC5kYXRQSwUGAAAAAAEAAQA9AAAAQwAAAAAA", "base64");

export async function checkWorldArchivesUi({ member, origin, evidenceRoot, report }) {
  for (const width of [1440, 390, 320]) {
    const created = await member.request.post(origin + "/api/game/create", { data: {
      title: "Minecraft world archive review", folder_location: "world-review-" + width + "-" + Date.now(),
      description: "Disposable world archives and modpack files for responsive review.",
    } });
    assert.equal(created.status(), 201);
    const id = (await created.json()).id;
    const page = await member.newPage(), requests = [], errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => requests.push(request.url()));
    await page.setViewportSize({ width, height: 1000 });
    try {
      assert.equal((await member.request.patch(origin + "/api/preferences", { data: {
        ui_welcome_completed: true, ui_theme: width === 1440 ? "dark" : "light",
      } })).status(), 200);
      const modpack = await member.request.post(origin + `/api/game/${id}/files/modpack`, {
        multipart: { file: { name: "Review-pack.zip", mimeType: "application/zip", buffer: zip } },
      });
      assert.equal(modpack.status(), 200);
      await page.goto(origin + "/games/" + id);
      await page.getByRole("heading", { name: "Minecraft world archive review", exact: true }).waitFor();
      assert.equal(requests.filter(url => url.endsWith("/archives/world_save")).length, 0);
      await page.getByRole("button", { name: "Worlds", exact: true }).click();
      const panel = page.locator(".worlds-panel");
      await panel.getByRole("button", { name: /Add a world/ }).waitFor();
      await panel.getByRole("heading", { name: "Modpack", exact: true }).waitFor();
      await panel.locator("input.ga-input").setInputFiles({ name: "Review-world.zip", mimeType: "application/zip", buffer: zip });
      const prompt = page.getByRole("dialog", { name: "Name this world" });
      await prompt.getByRole("textbox").fill("Main world");
      const uploaded = page.waitForResponse(response => response.request().method() === "POST"
        && response.url().endsWith("/archives/world_save"));
      await prompt.getByRole("button", { name: "Upload", exact: true }).click();
      assert.equal((await uploaded).status(), 200);
      const card = panel.locator(".ac").filter({ hasText: "Main world" });
      await card.waitFor();
      assert.equal(await panel.getByRole("button", { name: /Render|View Map/ }).count(), 0);
      assert.equal(await panel.locator("iframe").count(), 0);
      await card.locator(".ac-thumb").click();
      const dialog = page.getByRole("dialog", { name: "Main world", exact: true });
      await dialog.waitFor();
      const versionAdded = page.waitForResponse(response => response.request().method() === "POST"
        && /\/archives\/[^/]+\/versions$/.test(response.url()));
      await dialog.locator("input.aed-input").setInputFiles({ name: "Review-world-v2.zip", mimeType: "application/zip", buffer: zip });
      assert.equal((await versionAdded).status(), 200);
      await dialog.getByText("2 versions", { exact: true }).waitFor();
      assert.equal(await dialog.getByRole("link", { name: "Download", exact: true }).count(), 2);
      const archives = await (await member.request.get(origin + `/api/game/${id}/archives/world_save`)).json();
      assert.equal(archives.length, 1);
      for (const version of archives[0].versions) {
        const download = await member.request.get(origin + version.url);
        assert.equal(download.status(), 200);
        assert.deepEqual(await download.body(), zip);
      }
      await dialog.getByLabel("Note", { exact: true }).fill("Keep both backup versions");
      const saved = page.waitForResponse(response => response.request().method() === "PATCH"
        && response.url().endsWith("/archives/" + archives[0].id));
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      assert.equal((await saved).status(), 200);
      await dialog.waitFor({ state: "hidden" });
      await card.getByText("2 versions", { exact: true }).waitFor();
      if (width < 500) {
        for (const title of [card.locator(".ac-title"), panel.locator(".modpack-block .title")]) {
        const contrast = await title.evaluate(element => {
          const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
          const luminance = value => rgb(value).map(v => v / 255).map(v =>
            v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
            .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
          const text = luminance(getComputedStyle(element).color);
          const background = luminance(getComputedStyle(element.closest(".ac, .tile")).backgroundColor);
          return (Math.max(text, background) + .05) / (Math.min(text, background) + .05);
        });
        assert(contrast >= 4.5, "Archive and modpack titles remain legible in light mode");
        }
      }
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      const dismiss = page.getByRole("button", { name: /^Dismiss Uploading/ });
      while (await dismiss.count()) await dismiss.first().click();
      await panel.getByRole("heading", { name: "Worlds", exact: true }).click();
      if (width < 500) {
        const top = await panel.evaluate(element => element.getBoundingClientRect().top + scrollY);
        await page.evaluate(top => scrollTo(0, top - 160), top);
      }
      await page.screenshot({ path: path.join(evidenceRoot, `worlds-${width}.png`), fullPage: width >= 500 });

      // Existing shared links keep selecting the renamed tab after reload.
      await page.goto(origin + "/games/" + id + "?tab=World%20Map");
      await panel.getByText("Main world", { exact: true }).waitFor();
      await card.locator(".ac-thumb").click();
      await dialog.waitFor();
      assert.equal(await dialog.getByLabel("Note", { exact: true }).inputValue(), "Keep both backup versions");
      const box = await dialog.boundingBox();
      assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= width + 1 && box.y + box.height <= 1001);
      assert((await dialog.locator(".aed-v-main").first().boundingBox()).width >= 100,
        "Version dates and filenames have readable space on phones");
      await page.screenshot({ path: path.join(evidenceRoot, `versions-${width}.png`), fullPage: false });
      await dialog.getByRole("button", { name: "Delete world", exact: true }).click();
      const confirm = page.getByRole("dialog", { name: "Move to trash", exact: true });
      const deleted = page.waitForResponse(response => response.request().method() === "DELETE"
        && response.url().endsWith("/archives/" + archives[0].id));
      await confirm.getByRole("button", { name: "Move to trash", exact: true }).click();
      assert.equal((await deleted).status(), 200);
      await panel.getByRole("button", { name: "Deleted (1)", exact: true }).click();
      const restored = page.waitForResponse(response => response.request().method() === "POST"
        && response.url().endsWith("/archives/" + archives[0].id + "/restore"));
      await panel.getByRole("button", { name: "Restore", exact: true }).click();
      assert.equal((await restored).status(), 200);
      await card.getByText("2 versions", { exact: true }).waitFor();
      const active = await (await member.request.get(origin + `/api/game/${id}/archives/world_save`)).json();
      assert.equal(active[0].note, "Keep both backup versions");
      assert.equal(active[0].versions.length, 2);
      assert.equal(requests.filter(url => url.includes("/world-map/")).length, 0);
      assert.deepEqual(errors, []);
      report.passed.push({ width, on_demand: true, upload: true, versions_and_downloads: true,
        note_persisted: true, legacy_tab_link: true, trash_restore: true, modpack_visible: true,
        renderer_requests: 0, overflow: false, console_errors: 0 });
    } finally {
      await page.close();
    }
  }
}
