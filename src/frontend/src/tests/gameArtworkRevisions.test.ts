import { afterEach, describe, expect, it, vi } from "vitest";
import {
  attachGameAssetFromUrl,
  mapBackendGame,
  uploadGameAsset,
} from "../services/games";
import type { BackendGame } from "../services/games";
import {
  gameAssetUrl,
  rememberArtworkRevision,
  sizedAssetUrl,
} from "../utils/gameImages";

const REVISION = "0123456789abcdef0123456789abcdef";

function reply(revision: string) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      game_id: "g",
      asset_kind: "key_art",
      path: "p",
      status: "saved",
      revision,
    }),
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("server artwork revisions", () => {
  it("keeps plain URLs until the server has named the bytes", () => {
    expect(gameAssetUrl("fresh-game", "banner")).toBe(
      "/api/game/fresh-game/assets/banner",
    );
  });

  it("adds the server revision after an upload and still sizes the URL", async () => {
    vi.stubGlobal("fetch", reply(REVISION));
    await uploadGameAsset("up-game", "key_art", new File(["x"], "a.png"));
    expect(gameAssetUrl("up-game", "key_art")).toBe(
      `/api/game/up-game/assets/key_art?v=${REVISION}`,
    );
    expect(gameAssetUrl("up-game", "banner")).toBe(
      "/api/game/up-game/assets/banner",
    );
    expect(sizedAssetUrl(gameAssetUrl("up-game", "key_art"), 400)).toBe(
      `/api/game/up-game/assets/key_art?v=${REVISION}&w=400`,
    );
  });

  it("follows a replacement downloaded from a URL", async () => {
    const next = "fedcba9876543210fedcba9876543210";
    vi.stubGlobal("fetch", reply(next));
    await attachGameAssetFromUrl("dl-game", "banner", "https://example.test/a");
    expect(gameAssetUrl("dl-game", "banner")).toContain(`?v=${next}`);
  });

  it("ignores anything that is not a revision", () => {
    rememberArtworkRevision("odd-game", "icon", undefined);
    rememberArtworkRevision("odd-game", "icon", "../x?y");
    rememberArtworkRevision("odd-game", "icon", Date.now());
    expect(gameAssetUrl("odd-game", "icon")).toBe(
      "/api/game/odd-game/assets/icon",
    );
  });

  it("maps game payloads to the versioned artwork URLs", () => {
    rememberArtworkRevision("map-game", "banner", REVISION);
    const game = mapBackendGame({
      id: "map-game",
      title: "T",
      status: "BACKLOG",
    } as unknown as BackendGame);
    expect(game.bannerImageUrl).toBe(
      `/api/game/map-game/assets/banner?v=${REVISION}`,
    );
    expect(game.coverImageUrl).toBe("/api/game/map-game/assets/key_art?w=400");
  });
});
