import { describe, expect, it } from "vitest";
import { launchTargets } from "../utils/gameLaunch";

const GUID = "0A1B2C3D-4E5F-6789-ABCD-EF0123456789";

describe("game launch links", () => {
  it("builds fixed-scheme links from valid identifiers", () => {
    expect(
      launchTargets({
        playniteGuid: GUID,
        providerIds: { steam: "620", gog: "1207658924" },
      }).map((target) => target.uri),
    ).toEqual([
      "playnite://playnite/start/0a1b2c3d-4e5f-6789-abcd-ef0123456789",
      "steam://rungameid/620",
      "goggalaxy://openGameView/1207658924",
    ]);
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
});
