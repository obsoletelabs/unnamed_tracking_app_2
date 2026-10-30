import { shallowRef } from "vue";

export const GENRE_OPTIONS = [
  "Action",
  "Adventure",
  "RPG",
  "Shooter",
  "Platformer",
  "Puzzle",
  "Strategy",
  "Simulation",
  "Sports",
  "Racing",
  "Fighting",
  "Horror",
  "Survival",
  "Stealth",
  "Sandbox",
  "Open World",
  "Metroidvania",
  "Roguelike",
  "Rhythm",
  "Visual Novel",
  "Party",
  "Card & Board",
  "MMO",
  "Tactics",
  "Educational",
];

// Game tags are stored exactly as each metadata provider, import or person
// wrote them, and nothing here rewrites stored data. Filters read the tags
// through three deterministic rules (see "Tag normalization" in the games
// user guide, which also lists how to roll a rule back):
//
// 1. Spelling. Letter case, accents, spacing and punctuation are ignored,
//    "&" reads as "and", and a leading source or category label such as
//    "Genre:" or "Steam:" is dropped. "Rogue-like", "roguelike" and
//    "Steam: Roguelike" are one choice; "Roguelite" stays a different one.
// 2. Synonyms. Different names for exactly the same kind of game share one
//    choice, spelled as below. Only true synonyms belong here, never a
//    related or overlapping genre.
// 3. Broader genres. A narrower tag keeps its own choice and also counts as
//    the shared genres it belongs to, so IGDB's "Hack and slash/Beat 'em up"
//    (it has no "Action" genre) is found by Action. Counting is transitive.

// Labels some providers and imports put in front of a tag. Kept to a known
// list so a colon inside an ordinary tag is left alone.
const SOURCE_PREFIX =
  /^(?:\s*(?:genres?|tags?|themes?|steam|igdb|gog)\s*:)+\s*/i;

// Keys are spelling keys (see tagSpellingKey) of the variant spelling.
const TAG_SYNONYMS: Record<string, string> = {
  // other providers' names for the shared genres
  roleplaying: "RPG",
  roleplayingrpg: "RPG",
  roleplayinggame: "RPG",
  platform: "Platformer",
  simulator: "Simulation",
  sport: "Sports",
  cardandboardgame: "Card & Board",
  massivelymultiplayer: "MMO",
  partygame: "Party",
  // one sub-genre under several names
  race: "Racing",
  fps: "First-Person Shooter",
  rts: "Real-Time Strategy",
  realtimestrategyrts: "Real-Time Strategy",
  tbs: "Turn-Based Strategy",
  turnbasedstrategytbs: "Turn-Based Strategy",
  sciencefiction: "Sci-fi",
  shmup: "Shoot 'Em Up",
  soccer: "Football (Soccer)",
};

// Keys are spelling keys of the narrower tag after synonyms are applied.
const BROADER_GENRES: Record<string, string[]> = {
  hackandslashbeatemup: ["Action"],
  hackandslash: ["Action"],
  beatemup: ["Action"],
  brawler: ["Action"],
  actionadventure: ["Action", "Adventure"],
  actionrpg: ["Action", "RPG"],
  actionroguelike: ["Action", "Roguelike"],
  shooter: ["Action"],
  firstpersonshooter: ["Shooter"],
  thirdpersonshooter: ["Shooter"],
  twinstickshooter: ["Shooter"],
  arenashooter: ["Shooter"],
  heroshooter: ["Shooter"],
  lootershooter: ["Shooter"],
  shootemup: ["Shooter"],
  fighting: ["Action"],
  "2dfighter": ["Fighting"],
  "3dfighter": ["Fighting"],
  jrpg: ["RPG"],
  crpg: ["RPG"],
  partybasedrpg: ["RPG"],
  tacticalrpg: ["RPG", "Tactics"],
  strategyrpg: ["RPG", "Strategy"],
  realtimestrategy: ["Strategy"],
  turnbasedstrategy: ["Strategy"],
  grandstrategy: ["Strategy"],
  "4x": ["Strategy"],
  tactical: ["Tactics"],
  turnbasedtactics: ["Tactics"],
  "2dplatformer": ["Platformer"],
  "3dplatformer": ["Platformer"],
  precisionplatformer: ["Platformer"],
  puzzleplatformer: ["Puzzle", "Platformer"],
  drivingracing: ["Racing"],
  music: ["Rhythm"],
  musicrhythm: ["Rhythm"],
  cardgame: ["Card & Board"],
  boardgame: ["Card & Board"],
  mmorpg: ["MMO", "RPG"],
  survivalhorror: ["Survival", "Horror"],
  psychologicalhorror: ["Horror"],
  openworldsurvivalcraft: ["Open World", "Survival"],
  pointandclick: ["Adventure"],
  colonysim: ["Simulation"],
  farmingsim: ["Simulation"],
  lifesim: ["Simulation"],
  spacesim: ["Simulation"],
  automobilesim: ["Simulation"],
  roguelikedeckbuilder: ["Roguelike"],
};

function tagLabel(tag: string): string {
  return tag.replace(SOURCE_PREFIX, "").replace(/\s+/g, " ").trim();
}

// Rule 1 only: two tags with the same spelling key are the same tag.
export function tagSpellingKey(tag: string): string {
  return tagLabel(tag)
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^\p{L}\p{N}]/gu, "");
}

const SHARED_BY_KEY = new Map(GENRE_OPTIONS.map((g) => [tagSpellingKey(g), g]));

interface TagIdentity {
  key: string;
  label: string;
}

// The person's own tag catalogue (#267): a spelling they, or the server's
// defaults, map to another label. Keys are the server's folded spellings.
// Reading it through a ref lets the pickers update when the rules load.
const catalogueRules = shallowRef<ReadonlyMap<string, string>>(new Map());

export function catalogueSpellingKey(tag: string): string {
  return tag
    .normalize("NFKC")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function setCatalogueRules(rules: Record<string, string>): void {
  catalogueRules.value = new Map(Object.entries(rules));
}

function identify(tag: string): TagIdentity | null {
  const key = tagSpellingKey(tag);
  if (!key) return null;
  const mapped = catalogueRules.value.get(catalogueSpellingKey(tag));
  if (mapped) {
    const mappedKey = tagSpellingKey(mapped);
    if (mappedKey) return { key: mappedKey, label: mapped };
  }
  const synonym = TAG_SYNONYMS[key];
  if (synonym) return { key: tagSpellingKey(synonym), label: synonym };
  return { key, label: SHARED_BY_KEY.get(key) ?? tagLabel(tag) };
}

// The key filters compare: spelling and synonyms applied, empty when the tag
// has no letters or digits at all.
export function tagKey(tag: string): string {
  return identify(tag)?.key ?? "";
}

export function sameTag(a: string, b: string): boolean {
  const key = tagKey(a);
  return !!key && key === tagKey(b);
}

// How a filter value is written in URLs, presets and pills.
export function canonicalTag(tag: string): string | undefined {
  return identify(tag)?.label;
}

function addWithBroader(
  identity: TagIdentity,
  byKey: Map<string, string>,
): void {
  if (byKey.has(identity.key)) return;
  byKey.set(identity.key, identity.label);
  for (const genre of BROADER_GENRES[identity.key] ?? []) {
    const broader = identify(genre);
    if (broader) addWithBroader(broader, byKey);
  }
}

function genresByKey(tags: string[]): Map<string, string> {
  const byKey = new Map<string, string>();
  for (const tag of tags) {
    const identity = identify(tag);
    if (identity) addWithBroader(identity, byKey);
  }
  return byKey;
}

// Every genre a game's tags stand for: each tag itself (spelled like the
// shared list or the synonym it is, so "action" reads "Action") plus the
// broader genres it counts as, without duplicates.
export function gameGenres(tags: string[]): string[] {
  return [...genresByKey(tags).values()];
}

export function hasGenre(tags: string[], genre: string): boolean {
  const key = tagKey(genre);
  return !!key && genresByKey(tags).has(key);
}

// Filter choices: the shared genres plus every genre found in the library.
export function genreOptionsFor(tagLists: string[][]): string[] {
  return gameGenres([...GENRE_OPTIONS, ...tagLists.flat()]).sort();
}

export interface TagFilterOption {
  // the filter value, as written in URLs, presets and pills
  value: string;
  // games in the library this choice matches
  count: number;
  // one of the shared genres rather than a tag found in the library
  shared: boolean;
  // other spellings in the library that this choice also matches
  spellings: string[];
  // shared genres this tag also counts as
  broader: string[];
}

interface OptionDraft {
  key: string;
  count: number;
  labels: Map<string, number>;
  spellings: Map<string, string>;
}

// The tag picker's choices, one per distinct tag after the rules above, with
// how many games each matches. Shared genres come first (A–Z), then the
// library's own tags, most used first. A tag found under several spellings is
// written the way most games spell it.
export function tagFilterOptions(tagLists: string[][]): TagFilterOption[] {
  const drafts = new Map<string, OptionDraft>();
  const draft = (key: string): OptionDraft => {
    let found = drafts.get(key);
    if (!found) {
      found = { key, count: 0, labels: new Map(), spellings: new Map() };
      drafts.set(key, found);
    }
    return found;
  };
  for (const tags of tagLists) {
    for (const raw of tags) {
      const identity = identify(raw);
      if (!identity) continue;
      const entry = draft(identity.key);
      entry.labels.set(
        identity.label,
        (entry.labels.get(identity.label) ?? 0) + 1,
      );
      const spelling = raw.replace(/\s+/g, " ").trim();
      entry.spellings.set(spelling.toLowerCase(), spelling);
    }
    for (const key of genresByKey(tags).keys()) draft(key).count++;
  }
  const options: TagFilterOption[] = [];
  for (const entry of drafts.values()) {
    if (!entry.count) continue;
    const shared = SHARED_BY_KEY.get(entry.key);
    const value =
      shared ??
      [...entry.labels.entries()].sort(
        ([a, aCount], [b, bCount]) => bCount - aCount || a.localeCompare(b),
      )[0]?.[0] ??
      entry.key;
    const broader = new Map<string, string>();
    addWithBroader({ key: entry.key, label: value }, broader);
    broader.delete(entry.key);
    options.push({
      value,
      count: entry.count,
      shared: !!shared,
      spellings: [...entry.spellings.values()]
        .filter((s) => s.toLowerCase() !== value.toLowerCase())
        .sort((a, b) => a.localeCompare(b)),
      broader: [...broader.values()],
    });
  }
  return options.sort(
    (a, b) =>
      Number(b.shared) - Number(a.shared) ||
      (a.shared ? 0 : b.count - a.count) ||
      a.value.localeCompare(b.value),
  );
}
