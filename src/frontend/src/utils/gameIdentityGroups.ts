import type { Game } from "../types/game";
import type { LibraryLayout, LayoutRelation } from "../services/gameIdentity";
import { gameOwnershipGroups } from "./gameOwnership";

export interface NestedGame {
  game: Game;
  kind: string;
  label: string;
}

export interface LibraryGrouping {
  /** One entry per logical game; versions and owned copies are folded in. */
  entries: Game[];
  /** Every record an entry stands for (owned copies and versions), entry first. */
  members: Map<string, Game[]>;
  /** The entry's versions (entry included) when more than one share the umbrella. */
  versions: Map<string, Game[]>;
  /** Owned copies grouped under the entry's own record (#56). */
  ownedCopies: Map<string, number>;
  /** Related games listed under the entry instead of on their own (display "nest"). */
  nested: Map<string, NestedGame[]>;
  /** The entry that stands for a game, whether or not it is listed on its own. */
  entryOf: Map<string, string>;
  /**
   * Reconciled playtime of the whole group behind an entry whose copies and versions
   * are all folded into it, as the game page shows it (#277). Entries not listed here
   * show their own record's playtime.
   */
  playtimeSeconds: Map<string, number>;
}

/**
 * Library presentation: one entry per logical game, built from the owned-copy
 * groups plus the identity layout. Original records are never changed, and a
 * version whose umbrella is missing (Trash, a failed layout request) stays
 * visible on its own. `showAll` is used while searching: related games that
 * would be nested or hidden then appear when they match, but merged versions
 * still surface through their umbrella.
 */
export function libraryGrouping(
  games: Game[],
  layout: LibraryLayout | null,
  options: { showAll?: boolean } = {},
): LibraryGrouping {
  const owned = gameOwnershipGroups(games);
  const byId = new Map(games.map((game) => [game.id, game]));
  const entryOf = new Map<string, string>();
  for (const [id, group] of owned.members)
    for (const game of group) entryOf.set(game.id, id);
  const members = new Map<string, Game[]>(
    [...owned.members].map(([id, group]) => [id, [...group]]),
  );
  const ownedCopies = new Map<string, number>(
    [...owned.members].map(([id, group]) => [id, group.length]),
  );
  const versions = new Map<string, Game[]>();
  const hidden = new Set<string>();

  if (layout && layout.mode === "grouped") {
    for (const group of layout.groups) {
      const head = entryOf.get(group.umbrellaId);
      if (head === undefined || !members.has(head)) continue;
      const folded: string[] = [];
      for (const member of group.members) {
        if (member.display === "separate") continue;
        const entry = entryOf.get(member.gameId);
        if (entry === undefined || entry === head || hidden.has(entry))
          continue;
        folded.push(entry);
        hidden.add(entry);
      }
      if (!folded.length) continue;
      for (const entry of folded)
        members.get(head)!.push(...members.get(entry)!);
      versions.set(
        head,
        [head, ...folded].map((id) => byId.get(id)!),
      );
    }
  }

  const playtimeSeconds = new Map<string, number>();
  if (layout) {
    for (const group of layout.groups) {
      const head = entryOf.get(group.umbrellaId);
      if (head === undefined || group.playtimeSeconds === null) continue;
      // a version kept as its own card keeps its own hours; only a group that
      // is entirely folded behind this entry can speak for all of them
      const standsAlone = group.members.some((member) => {
        const entry = entryOf.get(member.gameId);
        return (
          entry !== undefined &&
          entry !== head &&
          !(layout.mode === "grouped" && member.display !== "separate")
        );
      });
      if (!standsAlone) playtimeSeconds.set(head, group.playtimeSeconds);
    }
  }

  const nested = new Map<string, NestedGame[]>();
  if (layout && !options.showAll) {
    for (const relation of layout.related) {
      const child = entryOf.get(relation.fromId);
      const parent = entryOf.get(relation.toId);
      if (!child || !parent || child === parent || hidden.has(child)) continue;
      if (relation.display === "hidden") hidden.add(child);
      else if (relation.display === "nest" && !hidden.has(parent)) {
        hidden.add(child);
        nestUnder(nested, parent, relation, byId.get(child)!);
      }
    }
  }
  for (const [parent, children] of nested)
    if (hidden.has(parent)) {
      // the parent itself went away: never lose access to the children
      for (const child of children) hidden.delete(child.game.id);
      nested.delete(parent);
    }

  const entries = owned.entries.filter((game) => !hidden.has(game.id));
  for (const id of [...members.keys()]) if (hidden.has(id)) members.delete(id);
  return {
    entries,
    members,
    versions,
    ownedCopies,
    nested,
    entryOf,
    playtimeSeconds,
  };
}

function nestUnder(
  nested: Map<string, NestedGame[]>,
  parent: string,
  relation: LayoutRelation,
  game: Game,
) {
  const list = nested.get(parent) ?? [];
  list.push({ game, kind: relation.kind, label: relation.label });
  nested.set(parent, list);
}

export function relationshipKindLabel(kind: string, label = ""): string {
  if (kind === "custom" && label) return label;
  const words = kind.replace(/_of$/, "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * What tells a version apart from its umbrella: "Java Edition" for "Minecraft
 * Java Edition" under "Minecraft", "Bedrock Edition" under "Minecraft Java
 * Edition" (the words the titles share are dropped); else the platform.
 */
export function versionLabel(version: Game, umbrella: Game): string {
  const own = version.title.trim().split(/\s+/);
  const base = umbrella.title.trim().split(/\s+/);
  let shared = 0;
  while (
    shared < own.length &&
    shared < base.length &&
    own[shared].toLowerCase() === base[shared].toLowerCase()
  )
    shared++;
  const rest = own
    .slice(shared)
    .join(" ")
    .replace(/^[:–—-]+\s*/, "")
    .trim();
  if (shared > 0 && rest) return rest;
  return version.platforms[0]?.platform || version.source || version.title;
}
