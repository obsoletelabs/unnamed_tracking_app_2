// @vitest-environment happy-dom
// Related games (DLC, remakes, editions) link both ways from the game page.
import { flushPromises, mount, RouterLinkStub } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RelatedGamesPanel from "../components/game/RelatedGamesPanel.vue";
import * as identity from "../services/gameIdentity";
import type {
  IdentityRelationship,
  IdentitySources,
} from "../services/gameIdentity";
import { relatedGameLinks } from "../utils/identityTabs";

vi.mock("../services/gameIdentity", async (original) => ({
  ...(await original<typeof import("../services/gameIdentity")>()),
  fetchIdentitySources: vi.fn(),
}));

const edge = (
  id: string,
  kind: string,
  from: string,
  to: string,
  over: Partial<IdentityRelationship> = {},
): IdentityRelationship => ({
  id,
  kind,
  label: "",
  from_id: from,
  to_id: to,
  display: "default",
  origin: "ingest",
  created_at: 1,
  ended_at: null,
  ...over,
});

const TITLES = {
  base: "Harbor of Echoes",
  dlc: "Harbor of Echoes: Tidebound",
  remake: "Harbor of Echoes Remade",
  goty: "Harbor of Echoes GOTY",
  copy: "Harbor of Echoes (GOG)",
};

const sources = (
  gameId: string,
  relationships: IdentityRelationship[],
  versions: string[] = [gameId],
): IdentitySources =>
  ({
    game_id: gameId,
    umbrella_id: gameId,
    titles: TITLES,
    versions: versions.map((id) => ({
      game_id: id,
      title: TITLES[id as keyof typeof TITLES],
      platform: null,
      source: null,
      playnite_guid: null,
    })),
    observations: [],
    relationships,
    decisions: [],
    counters: {},
  }) as IdentitySources;

const harborEdges = [
  edge("r1", "dlc_of", "dlc", "base"),
  edge("r2", "remake_of", "remake", "base"),
  edge("r3", "version_of", "goty", "base"),
];

describe("relatedGameLinks", () => {
  it("lists the base game's DLC, remake and other edition from its own page", () => {
    const links = relatedGameLinks(sources("base", harborEdges), "base");
    expect(links.map((l) => [l.label, l.title, l.direction])).toEqual([
      ["DLC", TITLES.dlc, "incoming"],
      ["Other version", TITLES.goty, "incoming"],
      ["Remake", TITLES.remake, "incoming"],
    ]);
  });

  it("links a DLC back to its base game", () => {
    const links = relatedGameLinks(
      sources("dlc", [edge("r1", "dlc_of", "dlc", "base")]),
      "dlc",
    );
    expect(links).toEqual([
      {
        relationshipId: "r1",
        gameId: "base",
        title: TITLES.base,
        label: "DLC of",
        direction: "outgoing",
      },
    ]);
  });

  it("leaves out merged copies, ended links and links between its own versions", () => {
    const links = relatedGameLinks(
      sources(
        "base",
        [
          edge("m", "same_game", "copy", "base"),
          edge("old", "dlc_of", "dlc", "base", { ended_at: 5 }),
          edge("inner", "port_of", "copy", "base"),
          edge("custom", "custom", "remake", "base", { label: "Fan sequel" }),
        ],
        ["base", "copy"],
      ),
      "base",
    );
    expect(links.map((l) => [l.relationshipId, l.label])).toEqual([
      ["custom", "Fan sequel"],
    ]);
  });
});

describe("the Related games panel", () => {
  beforeEach(() => vi.clearAllMocks());
  const mountPanel = (gameId: string) =>
    mount(RelatedGamesPanel, {
      props: { gameId },
      global: { stubs: { RouterLink: RouterLinkStub } },
    });

  it("links every related game, base games first", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue(
      sources("goty", [
        edge("r3", "version_of", "goty", "base"),
        edge("r9", "dlc_of", "dlc", "goty"),
      ]),
    );
    const wrapper = mountPanel("goty");
    await flushPromises();
    const links = wrapper.findAllComponents(RouterLinkStub);
    expect(links.map((l) => l.props("to"))).toEqual([
      "/games/base",
      "/games/dlc",
    ]);
    expect(links[0].text()).toContain("Version of");
    expect(links[1].text()).toContain("DLC");
  });

  it("renders nothing when the game has no related games or loading fails", async () => {
    vi.mocked(identity.fetchIdentitySources).mockResolvedValue(
      sources("base", []),
    );
    expect(
      (
        await (async () => {
          const w = mountPanel("base");
          await flushPromises();
          return w;
        })()
      )
        .find("section")
        .exists(),
    ).toBe(false);
    vi.mocked(identity.fetchIdentitySources).mockRejectedValue(
      new Error("offline"),
    );
    const failed = mountPanel("base");
    await flushPromises();
    expect(failed.find("section").exists()).toBe(false);
  });
});
