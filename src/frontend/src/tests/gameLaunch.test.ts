import { describe, expect, it } from "vitest";
import { defaultTarget, launchTargets, targetLabel } from "../utils/gameLaunch";

const GUID = "0A1B2C3D-4E5F-6789-ABCD-EF0123456789";
const OWNED = {
  playniteGuid: GUID,
  providerIds: { steam: "620", gog: "1207658924" },
};

describe("game launch links", () => {
  it("lists every way to open an owned game, most useful first", () => {
    expect(
      launchTargets({ ...OWNED, status: "backlog" }).map((t) => [
        t.id,
        t.action,
        t.uri,
      ]),
    ).toEqual([
      [
        "playnite",
        "launch",
        "playnite://playnite/start/0a1b2c3d-4e5f-6789-abcd-ef0123456789",
      ],
      ["steam", "launch", "steam://rungameid/620"],
      ["gog", "open", "goggalaxy://openGameView/1207658924"],
      ["steam-store", "store", "https://store.steampowered.com/app/620/"],
    ]);
  });

  it("offers only the store page for a wishlisted game", () => {
    const targets = launchTargets({ ...OWNED, status: "wishlist" });
    expect(targets.map((t) => t.id)).toEqual(["steam-store"]);
    expect(launchTargets({ status: "wishlist" })).toEqual([]);
  });

  it("offers nothing for a game without launchable identifiers", () => {
    expect(launchTargets({})).toEqual([]);
    expect(launchTargets({ providerIds: { igdb: "1" } })).toEqual([]);
  });

  it("never turns unexpected stored values into a link", () => {
    expect(
      launchTargets({
        playniteGuid: "javascript:alert(1)",
        providerIds: { steam: "620/../../x", gog: "1 2" },
      }),
    ).toEqual([]);
  });

  it("picks the default: remembered, then the copy's source, then Playnite, Steam, GOG", () => {
    const targets = launchTargets({ ...OWNED, status: "backlog" });
    const pick = (source: string | null, remembered: string | null) =>
      defaultTarget(targets, { source }, remembered)?.id;
    expect(pick(null, null)).toBe("playnite");
    expect(pick("steam", null)).toBe("steam");
    expect(pick("steam", "gog")).toBe("gog");
    expect(pick(null, "removed-client")).toBe("playnite");
    const noPlaynite = launchTargets({
      providerIds: { steam: "620" },
      status: "backlog",
    });
    expect(defaultTarget(noPlaynite, { source: null }, null)?.id).toBe("steam");
  });

  it("makes a store page the default only when nothing can be launched", () => {
    const wishlisted = launchTargets({ ...OWNED, status: "wishlist" });
    expect(defaultTarget(wishlisted, { source: null }, null)?.id).toBe(
      "steam-store",
    );
    expect(defaultTarget([], { source: null }, null)).toBeNull();
  });

  it("labels each action", () => {
    const labels = launchTargets({ ...OWNED, status: "backlog" }).map(
      targetLabel,
    );
    expect(labels).toEqual([
      "Launch in Playnite",
      "Launch in Steam",
      "Open in GOG Galaxy",
      "Open Steam store page",
    ]);
  });

  it("finds the store IDs a library import recorded on the game's sources", () => {
    const observations = [
      { source: "playnite", externalId: GUID, available: true },
      { source: "Steam", externalId: "620", available: true },
    ];
    expect(
      launchTargets({ status: "backlog", source: "Steam" }, observations).map(
        (t) => t.id,
      ),
    ).toEqual(["playnite", "steam", "steam-store"]);
  });

  it("ignores unavailable or malformed source records", () => {
    const observations = [
      { source: "steam", externalId: "620", available: false },
      { source: "steam", externalId: "not-a-number", available: true },
      { source: "playnite", externalId: "nope", available: true },
      { source: "epic", externalId: "123", available: true },
    ];
    expect(launchTargets({ status: "backlog" }, observations)).toEqual([]);
  });

  it("prefers the game's own identifiers over a source's", () => {
    const targets = launchTargets(
      { status: "backlog", providerIds: { steam: "70" } },
      [{ source: "steam", externalId: "620", available: true }],
    );
    expect(targets.map((t) => t.uri)).toContain("steam://rungameid/70");
  });
});
