import { describe, expect, it } from "vitest";
import type { Game } from "../types/game";
import { gameOwnershipGroups } from "../utils/gameOwnership";

function game(
  id: string,
  parentGameId: string | null = null,
  relationshipType: Game["relationshipType"] = null,
): Game {
  return { id, parentGameId, relationshipType } as Game;
}

describe("owned-copy library presentation", () => {
  it("shows one main entry, retains copy records and keeps DLC separate", () => {
    const main = game("main"),
      steam = game("steam", "main", "owned_copy"),
      epic = game("epic", "main", "owned_copy"),
      dlc = game("dlc", "main", "dlc");
    const groups = gameOwnershipGroups([steam, dlc, main, epic]);
    expect(groups.entries.map((entry) => entry.id)).toEqual(["dlc", "main"]);
    expect(groups.members.get("main")).toEqual([main, steam, epic]);
    expect(groups.entries[1]).toBe(main);
    expect(steam.parentGameId).toBe("main");
  });

  it("keeps orphaned and malformed copies visible instead of losing access", () => {
    const orphan = game("orphan", "missing", "owned_copy"),
      self = game("self", "self", "owned_copy");
    expect(gameOwnershipGroups([orphan, self]).entries).toEqual([orphan, self]);
  });
});
