// Provider fixtures make timing/identity regressions repeatable. Authentication,
// navigation, media creation and persisted episode progress use the real app.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const [pluginsRoot, evidenceRoot, origin] = process.argv.slice(2);
assert(pluginsRoot && evidenceRoot && origin && process.env.UI_REVIEW_USERNAME && process.env.UI_REVIEW_PASSWORD,
  "Supply browser dependencies, evidence directory, disposable app and review credentials.");
const { chromium } = createRequire(path.resolve(pluginsRoot, "package.json"))("playwright");
await mkdir(evidenceRoot, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const admin = await browser.newContext();
const report = { host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null,
  provider_response_fixture: true, real_media_persistence: true, checks: [], screenshots: [] };
const created = [];
const seed = Date.now();
function candidate(type, width, older = false) {
  const identity = String(seed + width + (older ? 1 : 0));
  return { id: `${type}-${identity}`, title: "Dune", rank: older ? 1 : 0,
    external_id: identity, year: older ? 1984 : 2024, media_type: type,
    provider: `fixture-${type}`, provider_name: "Review provider", providers: [], alternate_titles: [],
    provider_ids: type === "movie" ? { imdb: `tt${identity}` }
      : type === "tv_show" ? { tvmaze: identity } : { anilist: identity, mal: String(seed + width + 100) },
    metadata: { description: "Selected adaptation. Late details are still arriving.",
      release_date: older ? "1984-12-14" : "2024-03-01", genres: ["Adventure"],
      episode_count: type === "movie" ? null : 11, runtime_minutes: 120,
      seasons: type === "tv_show" ? [
        { season_number: 1, title: "First season", episode_count: 5, air_date: null },
        { season_number: 2, title: "Second season", episode_count: 6, air_date: null },
      ] : [] }, assets: [] };
}
function sse(sessionId, event, id, extra = {}) {
  return `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify({ session_id: sessionId, event, id, ...extra })}\n\n`;
}
async function capture(page, file) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "No horizontal viewport overflow");
  await page.screenshot({ path: path.join(evidenceRoot, file), fullPage: !await page.getByRole("dialog").isVisible() });
  report.screenshots.push(file);
}
try {
  const login = await admin.request.post(`${origin}/api/auth/login`, {
    data: { username_or_email: process.env.UI_REVIEW_USERNAME, password: process.env.UI_REVIEW_PASSWORD },
  });
  assert.equal(login.status(), 200, "Disposable account must sign in");
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ storageState: await admin.storageState(), viewport: { width, height: 1000 } });
    try {
      const page = await context.newPage();
      const errors = [], starts = [], selections = [];
      page.on("pageerror", error => errors.push(String(error)));
      const sessions = new Map();
      let releaseLate;
      const late = new Promise(resolve => { releaseLate = resolve; });
      let unavailable = false;
      await page.route("**/api/metadata/sessions**", async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === "/api/metadata/sessions") {
          const body = request.postDataJSON();
          starts.push(body);
          if (unavailable && body.media_type === "movie")
            return route.fulfill({ status: 503, json: { detail: "Injected provider outage" } });
          const id = `${body.media_type}-${starts.length}`;
          const items = [candidate(body.media_type, width)];
          if (body.media_type === "movie") items.push(candidate("movie", width, true));
          sessions.set(id, { type: body.media_type, items, selected: false });
          return route.fulfill({ status: 201, json: { id, query: body.query, media_type: body.media_type, state: "searching", results: [], last_event_id: 0 } });
        }
        const id = url.pathname.split("/")[4], session = sessions.get(id);
        assert(session, "Fixture session must exist");
        if (request.method() === "DELETE") return route.fulfill({ status: 204 });
        if (url.pathname.endsWith("/selection")) {
          const chosen = session.items.find(item => item.id === request.postDataJSON().candidate_id);
          assert(chosen, "Select the exact typed candidate identity");
          selections.push({ type: session.type, candidate_id: chosen.id });
          session.selected = true;
          return route.fulfill({ json: chosen });
        }
        const after = Number(url.searchParams.get("after"));
        if (after > 0) {
          if (session.type === "tv_show") await late;
          const selected = session.items[0];
          const enriched = { ...selected, metadata: { ...selected.metadata, description: "Late enrichment retained your entered progress." } };
          return route.fulfill({ contentType: "text/event-stream", body:
            sse(id, "result_updated", 10, { result: enriched }) + sse(id, "selection_enrichment_completed", 11) });
        }
        // A slow movie response must not block TV results.
        if (session.type === "movie") await new Promise(resolve => setTimeout(resolve, 1500));
        let body = session.items.map((item, index) => sse(id, "result_added", index + 1, { result: item })).join("");
        body += sse(id, "search_completed", 4);
        return route.fulfill({ contentType: "text/event-stream", body });
      });
      const media = page.locator(".media-search-page");
      for (const [library, type, label] of [["movies", "movie", /Add Movie/i], ["tv", "tv_show", /Add (TV )?Show/i], ["anime", "anime", /Add Anime/i]]) {
        await page.goto(`${origin}/${library}`);
        await page.getByRole("heading", { name: library === "movies" ? "Movies" : library === "tv" ? "TV Shows" : "Anime", exact: true }).waitFor();
        if (!await page.getByRole("button", { name: label }).first().isVisible())
          await page.getByRole("button", { name: "Library controls", exact: true }).click();
        await page.getByRole("button", { name: label }).first().click();
        await page.waitForURL(`**/media/search?type=${type}`);
        assert.equal(await media.getByRole("button", { name: type === "movie" ? "Movies" : type === "tv_show" ? "TV shows" : "Anime", exact: true }).getAttribute("aria-pressed"), "true");
      }
      await media.getByRole("button", { name: "All media", exact: true }).click();
      const identity = await page.evaluate(() => performance.timeOrigin);
      await page.getByLabel("Search media by title").fill("Dune");
      await media.locator(".result").filter({ hasText: "TV show" }).first().waitFor();
      assert.equal(await media.locator(".result").filter({ hasText: "Movie ·" }).count(), 0, "Fast TV results precede the slow movie provider");
      await media.locator(".result").filter({ hasText: "Movie · 2024" }).waitFor();
      assert.equal(starts.length, 3);
      await media.getByRole("button", { name: "Movies", exact: true }).click();
      assert.equal(await media.locator(".result").count(), 2);
      await media.getByRole("button", { name: "All media", exact: true }).click();
      assert.equal(starts.length, 3, "Type filters must not start another search");
      await capture(page, `results-${width}.png`);
      for (const type of ["tv_show", "movie", "anime"]) {
        const label = type === "tv_show" ? "TV show" : type === "movie" ? "Movie" : "Anime";
        await media.locator(".result").filter({ hasText: `${label} · 2024` }).click();
        const dialog = page.getByRole("dialog", { name: "Add to library" });
        await dialog.waitFor();
        const rating = dialog.getByLabel("Your rating (0–10)");
        await rating.fill("8.5");
        await dialog.getByLabel("Start date").fill("2026-01-02");
        if (type === "tv_show") {
          await dialog.getByLabel("Status", { exact: true }).selectOption("watching");
          await dialog.getByLabel(/Episodes watched/).fill("12");
          assert(await dialog.getByRole("button", { name: "Add to library", exact: true }).isDisabled(), "Progress cannot exceed the known total");
          await dialog.getByLabel(/Episodes watched/).fill("8");
          releaseLate();
          await page.waitForFunction(() => !document.querySelector("dialog .hint"));
          assert.equal(await dialog.getByLabel(/Episodes watched/).inputValue(), "8", "Late metadata must preserve progress");
          assert.equal(await rating.inputValue(), "8.5");
          await capture(page, `add-${width}.png`);
        } else if (type === "anime") {
          await dialog.getByLabel("Status", { exact: true }).selectOption("completed");
          await rating.fill("");
          await dialog.getByLabel("Start date").fill("");
          assert(await dialog.getByRole("button", { name: "Add to library", exact: true }).isEnabled(), "Clearing optional fields remains valid");
        }
        const prefix = type === "tv_show" ? "tv" : type === "movie" ? "movie" : "anime";
        const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === `/api/${prefix}/create` && response.request().method() === "POST");
        await dialog.getByRole("button", { name: "Add to library", exact: true }).click();
        const response = await responsePromise;
        assert(response.ok(), `Real ${type} creation must succeed: ${response.status()} ${await response.text()}`);
        const saved = await response.json();
        created.push({ prefix, id: saved.id });
        await media.locator(".saved-message").waitFor();
        const detail = await admin.request.get(`${origin}/api/${prefix}/get/${saved.id}`);
        assert(detail.ok(), "Saved title is accessible");
        const persisted = await detail.json();
        assert.deepEqual(persisted.provider_ids, candidate(type, width).provider_ids);
        if (type === "tv_show") assert.deepEqual(persisted.seasons.map(season => season.episodes_watched), [5, 3]);
        if (type === "anime") {
          assert.equal(persisted.seasons[0].episodes_watched, 11);
          assert.equal(persisted.rating_overall, null);
          assert.equal(persisted.start_date, null);
        }
        if (type === "movie") assert.equal(persisted.release_date, "2024-03-01", "The newer homonym must be saved");
      }
      assert.deepEqual(selections.map(selection => selection.type), ["tv_show", "movie", "anime"]);
      assert.equal(starts.length, 3, "Selecting/saving must not repeat the title search");
      unavailable = true;
      await page.getByLabel("Search media by title").fill("Du");
      await media.locator(".warnings").filter({ hasText: "Movies: The server had a problem" }).waitFor();
      await media.locator(".result").filter({ hasText: "Anime ·" }).first().waitFor();
      assert.equal(await page.evaluate(() => performance.timeOrigin), identity, "Search/filter/add keep the same document");
      assert.deepEqual(errors, []);
      await capture(page, `partial-provider-${width}.png`);
      report.checks.push({ width, three_library_entry_points: true, progressive_results: true,
        local_type_filter: true, exact_homonym_identity: true, late_progress_preserved: true,
        invalid_progress_rejected: true, empty_optional_fields: true, persisted_media_types: 3,
        partial_provider_failure: true, document_preserved: true, page_errors: errors });
    } catch (error) {
      const page = context.pages()[0];
      if (page) {
        console.error(await page.locator("body").innerText());
        await page.screenshot({ path: path.join(evidenceRoot, `failure-${width}.png`), fullPage: true });
      }
      throw error;
    } finally { await context.close(); }
  }
  if (process.env.UI_REVIEW_LIVE_QUERY) {
    const context = await browser.newContext({ storageState: await admin.storageState() });
    try {
      const page = await context.newPage(), requests = [], errors = [];
      page.on("pageerror", error => errors.push(String(error)));
      page.on("request", request => {
        if (new URL(request.url()).pathname === "/api/metadata/sessions" && request.method() === "POST")
          requests.push(request.postDataJSON());
      });
      await page.goto(`${origin}/media/search`);
      await page.getByLabel("Search media by title").fill(process.env.UI_REVIEW_LIVE_QUERY);
      await page.getByText("Searching… Results appear as providers respond.").waitFor();
      await page.getByText("Searching… Results appear as providers respond.").waitFor({ state: "hidden", timeout: 45000 });
      const count = requests.length, types = {};
      for (const [type, label] of [["movie", "Movies"], ["tv_show", "TV shows"], ["anime", "Anime"]]) {
        await page.locator(".media-search-page").getByRole("button", { name: label, exact: true }).click();
        types[type] = await page.locator(".result").count();
      }
      assert.equal(requests.length, count, "Live type filtering must not send another search");
      assert.deepEqual(requests.map(request => request.media_type).sort(), ["anime", "movie", "tv_show"]);
      assert.deepEqual(errors, []);
      await writeFile(path.join(evidenceRoot, "live-providers.json"), JSON.stringify({
        host_source_head: process.env.UI_REVIEW_HOST_HEAD ?? null, provider_response_fixture: false,
        requests, counts: types, warnings: await page.locator(".warnings").allInnerTexts(), page_errors: errors,
      }, null, 2) + "\n");
    } finally { await context.close(); }
  }
  await writeFile(path.join(evidenceRoot, "conformance.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report));
} finally {
  // Only remove records created by this run in the disposable review account.
  for (const { prefix, id } of created) {
    const deleted = await admin.request.delete(`${origin}/api/${prefix}/delete/${id}`);
    assert(deleted.ok(), "Remove the review record from its library");
    const purged = await admin.request.delete(`${origin}/api/${prefix}/${id}/purge`);
    assert(purged.ok(), "Purge only the review record created by this run");
  }
  await admin.close();
  await browser.close();
}
