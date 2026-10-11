// Compiled Statistics UI with real APIs in a disposable account.
// Delays and a single 503 are explicitly injected transport fixtures.
import assert from "node:assert/strict";
import path from "node:path";

export async function checkStatisticsLoading({ browser, member, origin, evidenceRoot, report }) {
  const games = [];
  let movie;
  try {
    for (let index = 0; index < 2; index++) {
      const response = await member.request.post(origin + "/api/game/create", { data: {
        title: "Statistics review game " + index, platform: "PC", status: "PLAYING",
        folder_location: "statistics-" + Date.now() + "-" + index,
        playtime_seconds: 3600, description: "Unused detail text ".repeat(500),
        notes: "Private notes excluded from stats ".repeat(200),
      } });
      assert.equal(response.status(), 201); games.push((await response.json()).id);
    }
    const created = await member.request.post(origin + "/api/movie/create", { data: {
      title: "Statistics review movie", status: "WATCHED", runtime_minutes: 100,
    } });
    assert.equal(created.status(), 201); movie = (await created.json()).id;
    const full = await member.request.get(origin + "/api/media-stats");
    assert.equal(full.status(), 200);
    const legacy = await full.json();
    report.payload_bytes = { full: Buffer.byteLength(await full.text()) };
    for (const section of ["overview", "games", "movie", "tv", "anime"]) {
      const response = await member.request.get(origin + "/api/media-stats?section=" + section);
      assert.equal(response.status(), 200);
      assert.deepEqual(await response.json(), { [section]: legacy[section] });
      report.payload_bytes[section] = Buffer.byteLength(await response.text());
    }
    for (const width of [320, 390, 1440]) {
      const context = await browser.newContext({ storageState: await member.storageState(),
        viewport: { width, height: 1000 } });
      try {
        await context.request.patch(origin + "/api/preferences", { data: {
          ui_theme: width === 1440 ? "dark" : "light", ui_welcome_completed: true,
        } });
        await context.addInitScript(() => { localStorage.setItem("statsTab", "games"); });
        const page = await context.newPage(), errors = [], requested = [];
        page.on("pageerror", error => errors.push(String(error)));
        page.on("request", request => {
          if (new URL(request.url()).pathname === "/api/media-stats")
            requested.push(new URL(request.url()).searchParams.get("section"));
        });
        const cards = page.locator(".stats-summary .stat-card");
        const tabs = page.getByRole("group", { name: "Statistics sections", exact: true });
        const select = name => tabs.getByRole("button", { name, exact: true }).click();
        const ready = () => page.waitForFunction(() => [...document.querySelectorAll("button")]
          .some(button => button.textContent.trim() === "Refresh" && !button.disabled));
        await page.goto(origin + "/statistics");
        await cards.first().waitFor(); await ready();
        assert.deepEqual(requested, ["games"], "Initial saved tab requests only itself");
        const gameNumbers = await cards.allTextContents();
        await select("Movies"); await ready();
        assert.deepEqual(requested, ["games", "movie"]);
        const movieNumbers = await cards.allTextContents();
        await select("Games");
        assert.deepEqual(await cards.allTextContents(), gameNumbers);
        assert.deepEqual(requested, ["games", "movie"], "Visited tabs reuse this visit's data");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, "statistics-games-" + width + ".png"), fullPage: true });
        // A slow refresh retains the cards instead of rendering zeros or a blank page.
        let release, received;
        const held = new Promise(resolve => { release = resolve; });
        const started = new Promise(resolve => { received = resolve; });
        const delay = async route => {
          const response = await route.fetch(); received(); await held;
          await route.fulfill({ response });
        };
        await page.route("**/api/media-stats?section=games", delay);
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await started;
        assert.deepEqual(await cards.allTextContents(), gameNumbers);
        assert.equal(await page.getByRole("status").count(), 0);
        assert(await page.getByRole("button", { name: "Refreshing…", exact: true }).isDisabled());
        release(); await ready(); await page.unroute("**/api/media-stats?section=games", delay);
        await page.route("**/api/media-stats?section=games", route => route.fulfill({ status: 503, body: "Failure fixture" }));
        await page.getByRole("button", { name: "Refresh", exact: true }).click();
        await page.getByRole("alert").waitFor();
        assert((await page.getByRole("alert").innerText()).includes("Showing the last loaded numbers."));
        assert.deepEqual(await cards.allTextContents(), gameNumbers);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await page.screenshot({ path: path.join(evidenceRoot, "statistics-refresh-error-" + width + ".png"), fullPage: true });
        await page.unroute("**/api/media-stats?section=games");
        await page.getByRole("button", { name: "Refresh", exact: true }).click(); await ready();
        await page.getByRole("alert").waitFor({ state: "hidden" });
        // Navigation through the sidebar exercises the actual KeepAlive lifecycle.
        async function navigate(name) {
          const toggle = page.getByRole("button", { name: "Open menu", exact: true });
          if (await toggle.isVisible()) await toggle.click();
          await page.getByRole("link", { name, exact: true }).first().click();
        }
        await navigate("Home"); await page.waitForURL(origin + "/");
        const beforeReturn = requested.length;
        await navigate("Statistics"); await ready();
        assert.equal(requested.length, beforeReturn + 1);
        assert.deepEqual(await cards.allTextContents(), gameNumbers);
        const beforeMovieReturn = requested.length;
        await select("Movies"); await ready();
        assert.deepEqual(await cards.allTextContents(), movieNumbers);
        assert.equal(requested.length, beforeMovieReturn + 1, "Other cached tabs revalidate after returning");
        await select("Overview"); await ready();
        await page.getByText("Titles tracked", { exact: true }).waitFor();
        assert.equal(requested.at(-1), "overview");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        assert.deepEqual(errors, []);
        report.passed.push("Compiled real API, selected tabs, cache, delayed/error refresh, retry and navigation: " + width);
      } finally { await context.close(); }
    }
  } finally {
    for (const id of games) await member.request.delete(origin + "/api/game/delete/" + id);
    if (movie) await member.request.delete(origin + "/api/movie/delete/" + movie);
  }
}
