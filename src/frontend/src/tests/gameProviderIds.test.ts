import { describe, expect, it } from "vitest";
import { gameProviderLinks } from "../utils/gameProviderIds";
import { mergeProviderIds } from "../utils/mediaEditorFields";

describe("game provider identities", () => {
  it("keeps saved canonical and store IDs while adding missing provider IDs", () => {
    expect(
      mergeProviderIds(
        { atlas: "atlas:canonical", steam: "620" },
        { atlas: "atlas:other", steam: "999", igdb: "72" },
      ),
    ).toEqual({ atlas: "atlas:canonical", steam: "620", igdb: "72" });
  });

  it("builds links from the store catalogue and rejects unsafe IDs", () => {
    expect(
      gameProviderLinks({
        steam: "620",
        gog: "portal_2",
        epic: "../admin",
        igdb: "72",
      }),
    ).toEqual([
      {
        namespace: "steam",
        label: "Steam",
        id: "620",
        url: "https://store.steampowered.com/app/620",
      },
      {
        namespace: "gog",
        label: "GOG",
        id: "portal_2",
        url: "https://www.gog.com/game/portal_2",
      },
    ]);
  });
});
