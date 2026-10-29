// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import GameVersionsPanel from "../components/game/GameVersionsPanel.vue";
import GameIdentityDialog from "../components/game/GameIdentityDialog.vue";
import * as identity from "../services/gameIdentity";
import type {
  IdentitySources,
  UmbrellaOverview,
} from "../services/gameIdentity";

vi.mock("../services/gameIdentity", async (original) => ({
  ...(await original<typeof import("../services/gameIdentity")>()),
  fetchUmbrellaOverview: vi.fn(),
  fetchIdentitySources: vi.fn(),
  mergeGames: vi.fn(async () => {}),
  unmergeGame: vi.fn(async () => {}),
  separateGames: vi.fn(async () => {}),
  moveObservation: vi.fn(async () => {}),
  setRelationshipDisplay: vi.fn(async () => {}),
  endRelationship: vi.fn(async () => {}),
  adjustPlaytime: vi.fn(async () => {}),
  clearAdjustment: vi.fn(async () => {}),
  setCounterPriority: vi.fn(async () => {}),
  groupCounters: vi.fn(async () => {}),
  makeMainCopy: vi.fn(async () => {}),
  splitCopy: vi.fn(async () => {}),
}));
vi.mock("../services/games", () => ({
  fetchGames: vi.fn(async () => [
    { id: "dungeons", title: "Minecraft Dungeons", platforms: [] },
    { id: "bedrock", title: "Minecraft Bedrock Edition", platforms: [] },
  ]),
}));
const confirmMock = vi.hoisted(() => vi.fn(async () => true));
const pushMock = vi.hoisted(() => vi.fn(async () => {}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("../state/dialog", () => ({ useConfirm: () => confirmMock }));
vi.mock("../services/gameTracking", () => ({
  fetchCompletion: vi.fn(async () => ({
    completed: 0,
    of: 0,
    partial: false,
    allCompleted: false,
    summary: "",
    versions: [],
  })),
  fetchAchievementTotals: vi.fn(async () => ({
    umbrella: {
      total: 0,
      unlocked: 0,
      percent: 0,
      manual: 0,
      provider: 0,
      summary: null,
    },
    versions: [],
    manual: [],
  })),
}));
const UiModalStub = {
  props: ["title"],
  emits: ["close"],
  template:
    '<div role="dialog" :aria-label="title"><slot /><slot name="footer" /></div>',
};
const stubs = {
  UiModal: UiModalStub,
  RouterLink: { template: "<a><slot /></a>" },
};
const wrappers: VueWrapper[] = [];
afterEach(() => wrappers.splice(0).forEach((w) => w.unmount()));
beforeEach(() => vi.clearAllMocks());

const version = (over: Partial<UmbrellaOverview["versions"][number]>) => ({
  game_id: "java",
  title: "Minecraft Java Edition",
  label: "Java Edition",
  edition: null,
  platform: "PC",
  source: "playnite",
  status: "BEATEN",
  is_primary: true,
  display: "default" as const,
  relationship_id: null,
  owned: true,
  playtime_seconds: 200 * 3600,
  playtime_basis: "reconciled" as const,
  last_played_at: 500,
  achievements: { unlocked: 4, total: 10, manual_total: 0 },
  completed: true,
  completion_user_set: true,
  sources: ["playnite"],
  last_observed_at: 100,
  ...over,
});
function overview(
  versions = [
    version({}),
    version({
      game_id: "bedrock",
      title: "Minecraft Bedrock Edition",
      label: "Bedrock Edition",
      platform: "Xbox",
      is_primary: false,
      playtime_seconds: 50 * 3600,
      playtime_basis: "record",
      completed: false,
      sources: [],
      achievements: { unlocked: 0, total: 0, manual_total: 0 },
    }),
  ],
): UmbrellaOverview {
  return {
    game_id: "java",
    umbrella_id: "java",
    aggregate: {
      playtime_seconds: 250 * 3600,
      playtime_is_sum_of_versions: true,
      last_played_at: 900,
      version_count: versions.length,
      owned_count: versions.length,
      owned_platforms: ["PC", "Xbox"],
      completion: {
        summary: "Completed on 1 of 2 tracked versions",
        completed: 1,
        of: 2,
        partial: true,
        all_completed: false,
      },
      achievements: { total: 10, unlocked: 4, percent: 40, summary: null },
    },
    versions,
  };
}

function panel() {
  const wrapper = mount(GameVersionsPanel, {
    props: { gameId: "java", title: "Minecraft" },
    global: { stubs },
  });
  wrappers.push(wrapper);
  return wrapper;
}

it("shows overall values apart from each version's own and says which versions are partial", async () => {
  vi.mocked(identity.fetchUmbrellaOverview).mockResolvedValue(overview());
  const wrapper = panel();
  await flushPromises();
  expect(wrapper.get('[data-testid="overall-playtime"]').text()).toBe("250h");
  expect(wrapper.get('[data-testid="overall-completion"]').text()).toBe(
    "Completed on 1 of 2 tracked versions",
  );
  const rows = wrapper.findAll('[data-testid="version-row"]');
  expect(rows).toHaveLength(2);
  expect(rows[0].text()).toContain("200h");
  expect(rows[1].text()).toContain("50h");
  expect(rows[1].text()).toContain("Xbox");
  expect(rows[0].text()).toContain("Main");
  // copies are named by edition, store and platform, not by a bare title
  expect(rows[0].text()).toContain("Java Edition · Playnite · PC");
  expect(rows[1].text()).toContain("Bedrock Edition · Playnite · Xbox");
  expect(wrapper.text()).toContain("2 copies tracked as one game");
});

it("makes a copy main, lists it separately and removes it after confirming", async () => {
  vi.mocked(identity.fetchUmbrellaOverview).mockResolvedValue(
    overview([
      version({}),
      version({
        game_id: "bedrock",
        title: "Minecraft Bedrock Edition",
        label: "Bedrock Edition",
        is_primary: false,
        relationship_id: "edge-1",
      }),
    ]),
  );
  const wrapper = panel();
  await flushPromises();
  const row = wrapper.findAll('[data-testid="version-row"]')[1];
  const button = (label: string) =>
    row.findAll("button").find((b) => b.text() === label)!;
  await button("Make main").trigger("click");
  await flushPromises();
  expect(identity.makeMainCopy).toHaveBeenCalledWith("bedrock");
  expect(pushMock).toHaveBeenCalledWith("/games/bedrock");
  await button("List separately").trigger("click");
  await flushPromises();
  expect(identity.setRelationshipDisplay).toHaveBeenCalledWith(
    "edge-1",
    "link",
  );
  await button("Remove").trigger("click");
  await flushPromises();
  expect(confirmMock).toHaveBeenCalled();
  expect(identity.splitCopy).toHaveBeenCalledWith("bedrock");
  expect(wrapper.emitted("changed")).toHaveLength(3);
  // the main copy offers neither "Make main" nor a display change
  const mainRow = wrapper.findAll('[data-testid="version-row"]')[0];
  expect(mainRow.text()).not.toContain("Make main");
  expect(mainRow.text()).not.toContain("List separately");
});

it("tells a copy's own page which game it belongs to", async () => {
  vi.mocked(identity.fetchUmbrellaOverview).mockResolvedValue(overview());
  const wrapper = mount(GameVersionsPanel, {
    props: { gameId: "bedrock", title: "Minecraft Bedrock Edition" },
    global: { stubs },
  });
  wrappers.push(wrapper);
  await flushPromises();
  expect(wrapper.text()).toContain(
    "This is one copy of Minecraft Java Edition",
  );
});

it("keeps ordinary browsing quiet for a game with one record", async () => {
  vi.mocked(identity.fetchUmbrellaOverview).mockResolvedValue(
    overview([version({})]),
  );
  const wrapper = panel();
  await flushPromises();
  expect(wrapper.find('[data-testid="overall-playtime"]').exists()).toBe(false);
  expect(wrapper.text()).toContain("Add it as a copy");
  expect(wrapper.findAll("button").map((b) => b.text())).toEqual([
    "Add a copy",
    "Advanced",
  ]);
});

it("shows an error with a retry, and folds hidden versions behind a toggle", async () => {
  vi.mocked(identity.fetchUmbrellaOverview).mockRejectedValueOnce(
    new Error("Server unavailable"),
  );
  const wrapper = panel();
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("Server unavailable");
  const hidden = overview([
    version({}),
    version({
      game_id: "bedrock",
      title: "Bedrock",
      is_primary: false,
      display: "hidden",
    }),
  ]);
  vi.mocked(identity.fetchUmbrellaOverview).mockResolvedValue(hidden);
  await wrapper.get('[role="alert"] button').trigger("click");
  await flushPromises();
  expect(wrapper.findAll('[data-testid="version-row"]')).toHaveLength(1);
  const toggle = wrapper
    .findAll("button")
    .find((b) => /hidden/.test(b.text()))!;
  expect(toggle.attributes("aria-pressed")).toBe("false");
  await toggle.trigger("click");
  expect(wrapper.findAll('[data-testid="version-row"]')).toHaveLength(2);
});

function sources(): IdentitySources {
  return {
    game_id: "java",
    umbrella_id: "java",
    titles: {
      java: "Minecraft Java Edition",
      bedrock: "Minecraft Bedrock Edition",
      dungeons: "Minecraft Dungeons",
    },
    versions: [
      {
        game_id: "java",
        title: "Minecraft Java Edition",
        platform: "PC",
        source: "playnite",
        playnite_guid: "g-1",
      },
      {
        game_id: "bedrock",
        title: "Minecraft Bedrock Edition",
        platform: "Xbox",
        source: null,
        playnite_guid: null,
      },
    ],
    observations: [
      {
        observation_id: "o1",
        game_id: "java",
        source: "playnite",
        source_kind: "host",
        scope: "legacy",
        connection_label: null,
        disconnected: false,
        external_id: "g-1",
        title: "Minecraft",
        platform: "PC",
        edition: null,
        availability: "available",
        unavailable_since: null,
        last_observed_at: Math.floor(Date.now() / 1000) - 86400 * 3,
        revision: 1,
        facts: { playtime_seconds: 720000 },
      },
    ],
    relationships: [
      {
        id: "e1",
        kind: "same_game",
        label: "",
        from_id: "bedrock",
        to_id: "java",
        display: "default",
        origin: "user",
        created_at: 1,
        ended_at: null,
      },
      {
        id: "e2",
        kind: "dlc_of",
        label: "",
        from_id: "dungeons",
        to_id: "java",
        display: "default",
        origin: "user",
        created_at: 1,
        ended_at: null,
      },
    ],
    decisions: [
      {
        id: "d1",
        subject_kind: "identity_pair",
        first_game_id: "java",
        second_game_id: "bedrock",
        observation_id: null,
        outcome: "confirmed_same",
        actor_kind: "user",
        actor_detail: null,
        reason: null,
        rule_version: null,
        confidence: null,
        evidence: {},
        created_at: 1,
        superseded_at: null,
      },
      {
        id: "d2",
        subject_kind: "identity_pair",
        first_game_id: "java",
        second_game_id: "dungeons",
        observation_id: null,
        outcome: "confirmed_separate",
        actor_kind: "user",
        actor_detail: null,
        reason: null,
        rule_version: null,
        confidence: null,
        evidence: {},
        created_at: 2,
        superseded_at: null,
      },
      {
        id: "d3",
        subject_kind: "identity_pair",
        first_game_id: "java",
        second_game_id: "bedrock",
        observation_id: null,
        outcome: "automatic_same",
        actor_kind: "automatic",
        actor_detail: null,
        reason: "exact",
        rule_version: "1",
        confidence: 0.97,
        evidence: {
          matched: ["title", "platform"],
          conflicts: ["release date"],
        },
        created_at: 0,
        superseded_at: 1,
      },
    ],
    counters: {
      playtime: [
        {
          counter_id: "c1",
          game_id: "java",
          metric: "playtime",
          raw: 720000,
          effective: 684000,
          selected_source: "playnite",
          members: [
            {
              observation_id: "o1",
              source: "playnite",
              value: 720000,
              observed_at: 1,
              priority: 100,
              selected: true,
              available: true,
            },
            {
              observation_id: "o2",
              source: "manual",
              value: 700000,
              observed_at: 1,
              priority: 100,
              selected: false,
              available: true,
            },
          ],
          conflicts: [
            { source: "manual", value: 700000, reason: "lower priority" },
          ],
          adjustment: { offset: -36000, override: null },
        },
        {
          counter_id: "c2",
          game_id: "bedrock",
          metric: "playtime",
          raw: 180000,
          effective: 180000,
          selected_source: "xbox",
          members: [],
          conflicts: [],
          adjustment: null,
        },
      ],
    },
  };
}

async function dialog() {
  vi.mocked(identity.fetchIdentitySources).mockResolvedValue(sources());
  const wrapper = mount(GameIdentityDialog, {
    props: { gameId: "java", title: "Minecraft" },
    global: { stubs },
    attachTo: document.body,
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}
const tabButton = (w: VueWrapper, name: string) =>
  w.findAll(".seg-tab").find((b) => b.text() === name)!;
const button = (w: VueWrapper, name: RegExp | string) =>
  w
    .findAll("button")
    .find((b) =>
      typeof name === "string"
        ? b.text() === name
        : name.test(b.text() + (b.attributes("aria-label") ?? "")),
    )!;

it("merges a chosen game into the umbrella and reloads", async () => {
  const wrapper = await dialog();
  await wrapper
    .get("select[aria-label*='Merge another game']")
    .setValue("dungeons");
  await button(wrapper, "Merge").trigger("click");
  await flushPromises();
  expect(identity.mergeGames).toHaveBeenCalledWith(
    "dungeons",
    "java",
    false,
    "target",
    false,
  );
  expect(identity.fetchIdentitySources).toHaveBeenCalledTimes(2);
});

it("asks before reversing a keep-separate decision and retries with consent", async () => {
  vi.mocked(identity.mergeGames)
    .mockRejectedValueOnce(
      new Error("These games were marked as separate; confirm to reverse that"),
    )
    .mockResolvedValue();
  const wrapper = await dialog();
  await wrapper
    .get("select[aria-label*='Merge another game']")
    .setValue("dungeons");
  await button(wrapper, "Merge").trigger("click");
  await flushPromises();
  expect(confirmMock).toHaveBeenCalled();
  expect(identity.mergeGames).toHaveBeenLastCalledWith(
    "dungeons",
    "java",
    true,
    "target",
    false,
  );
});

it("shows a failed change in place without closing", async () => {
  vi.mocked(identity.unmergeGame).mockRejectedValueOnce(new Error("Nope"));
  const wrapper = await dialog();
  await button(wrapper, /Split Minecraft Bedrock/).trigger("click");
  await flushPromises();
  expect(confirmMock).toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toBe("Nope");
  expect(wrapper.emitted("close")).toBeUndefined();
});

it("splits a merged version after confirmation", async () => {
  const wrapper = await dialog();
  await button(wrapper, /Split Minecraft Bedrock/).trigger("click");
  await flushPromises();
  expect(identity.unmergeGame).toHaveBeenCalledWith("bedrock", true);
});

it("changes how a merged version and a relationship are presented", async () => {
  const wrapper = await dialog();
  const shown = wrapper
    .findAll("select")
    .find((s) => s.text().includes("As its own entry too"))!;
  await shown.setValue("link");
  await flushPromises();
  expect(identity.setRelationshipDisplay).toHaveBeenCalledWith("e1", "link");
  const rel = wrapper
    .findAll("select")
    .find((s) => s.text().includes("Nest under"))!;
  await rel.setValue("nest");
  await flushPromises();
  expect(identity.setRelationshipDisplay).toHaveBeenCalledWith("e2", "nest");
});

it("lists source records with freshness and Playnite evidence and moves one", async () => {
  const wrapper = await dialog();
  await tabButton(wrapper, "Sources").trigger("click");
  expect(wrapper.text()).toContain("Playnite GUID g-1");
  expect(wrapper.text()).toContain("3 days ago");
  expect(wrapper.text()).toContain("Playtime 200h");
  expect(wrapper.text()).toContain("No source has reported this record");
  await wrapper.get("select").setValue("bedrock");
  await button(wrapper, "Move").trigger("click");
  await flushPromises();
  expect(identity.moveObservation).toHaveBeenCalledWith("o1", "bedrock");
});

it("explains decisions and reverses a rejection only after confirmation", async () => {
  const wrapper = await dialog();
  await tabButton(wrapper, "Decisions").trigger("click");
  expect(wrapper.text()).toContain("Kept separate");
  expect(wrapper.text()).toContain(
    "Minecraft Java Edition and Minecraft Dungeons",
  );
  expect(wrapper.text()).toContain("1 replaced decisions");
  await button(wrapper, "Merge anyway").trigger("click");
  await flushPromises();
  expect(identity.mergeGames).toHaveBeenCalledWith("java", "dungeons", true);
});

it("groups counters, corrects playtime and removes the correction", async () => {
  const wrapper = await dialog();
  await tabButton(wrapper, "Counters").trigger("click");
  expect(wrapper.text()).toContain("Adjusted by you");
  expect(wrapper.text()).toContain("lower priority");
  await wrapper.get('input[step="0.1"]').setValue("150");
  await wrapper.get("form.form-inline").trigger("submit");
  await flushPromises();
  expect(identity.adjustPlaytime).toHaveBeenCalledWith("java", 540000, "c1");
  await button(wrapper, "Remove my correction").trigger("click");
  await flushPromises();
  expect(identity.clearAdjustment).toHaveBeenCalledWith("c1");
  const group = wrapper
    .findAll("select")
    .find((s) => s.text().includes("Keep independent"))!;
  await group.setValue("c2");
  await flushPromises();
  expect(identity.groupCounters).toHaveBeenCalledWith("c1", "c2");
});

it("is operable from the keyboard: tabs are buttons and Escape/Done close", async () => {
  const wrapper = await dialog();
  const tabs = wrapper.findAll(".seg-tab");
  expect(tabs.map((t) => t.element.tagName)).toEqual(Array(5).fill("BUTTON"));
  await button(wrapper, "Done").trigger("click");
  expect(wrapper.emitted("close")).toHaveLength(1);
});

it("shows manual tracking controls in the identity dialog", async () => {
  const wrapper = await dialog();
  await tabButton(wrapper, "Manual tracking").trigger("click");
  await flushPromises();
  expect(
    wrapper.findComponent({ name: "GameManualTrackingPanel" }).exists(),
  ).toBe(true);
});

it("can merge a record as the same copy seen by another source", async () => {
  const wrapper = await dialog();
  await wrapper
    .get("select[aria-label*='Merge another game']")
    .setValue("dungeons");
  await wrapper.get(".same-copy input").setValue(true);
  await button(wrapper, "Merge").trigger("click");
  await flushPromises();
  expect(identity.mergeGames).toHaveBeenCalledWith(
    "dungeons",
    "java",
    false,
    "target",
    true,
  );
});
