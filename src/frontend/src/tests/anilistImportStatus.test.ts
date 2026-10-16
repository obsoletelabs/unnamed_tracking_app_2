import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, ssrContextKey } from "vue";
import AniListImportSection from "../components/settings/AniListImportSection.vue";
import { DEFAULT_PREFERENCES, fetchPreferences } from "../services/preferences";
import type { Preferences } from "../services/preferences";
import { notificationPresentation } from "../utils/notificationPresentation";

vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(),
  queuePreferences: vi.fn(),
}));
const renderer = createRenderer<object, object>({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert: () => {},
  remove: () => {},
  setText: () => {},
  setElementText: () => {},
  patchProp: () => {},
  parentNode: () => null,
  nextSibling: () => null,
});
const apps: ReturnType<typeof renderer.createApp>[] = [];
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  vi.clearAllMocks();
});

async function status(changes: Partial<Preferences>): Promise<string> {
  vi.mocked(fetchPreferences).mockResolvedValue({
    ...DEFAULT_PREFERENCES,
    ...changes,
  });
  const app = renderer.createApp({
    ...AniListImportSection,
    render: () => null,
  });
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  await nextTick();
  await nextTick();
  return (app._instance as unknown as { setupState: { importStatus: string } })
    .setupState.importStatus;
}

it("keeps new accounts quiet until a scheduled import has run", async () => {
  expect(await status({})).toBe("");
});

it("reports the last successful scheduled import", async () => {
  expect(
    await status({
      anilist_import_last_run_at: 1_700_000_000,
      anilist_import_last_attempt_at: 1_700_000_000,
    }),
  ).toMatch(/^Last successful import: .+\.$/);
});

it("reports a failed attempt without hiding the previous success", async () => {
  const text = await status({
    anilist_import_last_run_at: 1_700_000_000,
    anilist_import_last_attempt_at: 1_700_090_000,
    anilist_import_last_error: "Could not reach AniList",
  });
  expect(text).toMatch(
    /^Scheduled import failed .+: Could not reach AniList\. It will try again at the next scheduled time\. Last successful import: .+\.$/,
  );
});

it("presents franchise listings in their existing inbox groups", () => {
  expect(notificationPresentation("franchise_season")).toEqual({
    label: "New season listed",
    tone: "blue",
    group: "seasons",
  });
  expect(notificationPresentation("franchise_extra").group).toBe("releases");
  expect(DEFAULT_PREFERENCES.notify_franchise_season).toBe(false);
  expect(DEFAULT_PREFERENCES.notify_franchise_extra).toBe(false);
});
