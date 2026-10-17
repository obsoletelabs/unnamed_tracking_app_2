<script setup lang="ts">
import {
  ref,
  computed,
  nextTick,
  onMounted,
  watch,
  proxyRefs,
  useId,
} from "vue";
import EditorShell from "./EditorShell.vue";
import GamePlayFields from "./game/GamePlayFields.vue";
import GameArtPicker from "./game/GameArtPicker.vue";
import GameCollectionsField from "./game/GameCollectionsField.vue";
import {
  attachGameAssetFromUrl,
  createGame,
  fetchGames,
  updateGame,
  uploadGameAsset,
} from "../services/games";
import type { MetadataSearchResult } from "../services/games";
import type {
  Game,
  GameStatus,
  AchievementsProvider,
  GameRelationshipType,
  GamePlayCompatibility,
} from "../types/game";
import type { GameLink, GameOwnership } from "../types/game";

import PageSettingsEditor from "./PageSettingsEditor.vue";
import { preferences } from "../state/preferences";
import { manualCollectionNames } from "../state/collectionMeta";
import { resolvePage } from "../utils/gamePage";
import type { PageOverrides, PageSettings } from "../utils/gamePage";
import { fetchMetadataProviders } from "../services/metadata";
import type { MetadataProviderStatus } from "../services/metadata";
import { localDateInputToUnixSeconds, toLocalDateInput } from "../utils/dates";
import { PRIORITY_OPTIONS, isFinished } from "../utils/priority";
import { RETRO_PLATFORM_OPTIONS } from "../utils/platforms";
import { sizedAssetUrl } from "../utils/gameImages";

import { useMetadataSearch } from "../composables/useMetadataSearch";
import { useMetadataComparison } from "../composables/useMetadataComparison";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import { useStagedArtwork } from "../composables/useStagedArtwork";
import MetadataComparison from "./MetadataComparison.vue";
import { gameMetadataResult } from "../utils/metadataCandidate";
import { useTitleProtection } from "../utils/titleProtection";
import { numberOrNull } from "../utils/numberInput";
import {
  startTask,
  updateTask,
  completeTask,
  errorTask,
  setTaskRetry,
} from "../state/taskProgress";

const props = defineProps<{
  game?: Game | null;
  quickAdd?: boolean;
}>();

const compact = ref(!!props.quickAdd && !props.game);

// for the "parent game" picker, fetched here rather than threaded as a
// prop through every place this modal is opened from (GameLibrary,
// GameDetail, CollectionDetail, HomeHub), so it works consistently
// regardless of caller
const availableParentGames = ref<Game[]>([]);
// manual collections this game can join; its own memberships until the
// library list arrives
const collectionChoices = ref<string[]>(
  manualCollectionNames(props.game ? [props.game] : []),
);
// the ISO 4217 codes the API accepts (#11), so a typo can't fail the save
const currencyCodes = ref<string[]>([]);
async function loadFullFormChoices() {
  try {
    const games = await fetchGames();
    availableParentGames.value = games.filter((g) => g.id !== props.game?.id);
    collectionChoices.value = manualCollectionNames(games);
  } catch {
    // the pickers just keep what they have, not worth failing the whole form
  }
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return;
  try {
    const response = await fetch("/api/currency-codes", {
      credentials: "include",
    });
    if (response.ok) {
      const body: { codes?: string[] } = await response.json();
      currencyCodes.value = body.codes ?? [];
    }
  } catch {
    // falls back to the free-text currency field
  }
}
onMounted(() => {
  if (!compact.value) void loadFullFormChoices();
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return;
  fetchMetadataProviders()
    .then((providers) => {
      metadataProviders.value = providers;
    })
    .catch(() => {
      // the hint just stays visible
    })
    .finally(() => {
      metadataProvidersLoaded.value = true;
    });
});

function openFullForm() {
  if (!compact.value) return;
  compact.value = false;
  activeTab.value = "Basics";
  lookupOpen.value = !title.value.trim();
  void loadFullFormChoices();
}

// suggestions only: any other system can still be typed in
const PLATFORM_SUGGESTIONS = [
  "PC",
  "PlayStation 5",
  "PlayStation 4",
  "PlayStation 3",
  "PS Vita",
  "Xbox Series X|S",
  "Xbox One",
  "Xbox 360",
  "Nintendo Switch",
  "Nintendo Switch 2",
  "Wii U",
  "Wii",
  "Nintendo 3DS",
  "Nintendo DS",
  "Steam Deck",
  "Mac",
  "Linux",
  "Android",
  "iOS",
  ...RETRO_PLATFORM_OPTIONS,
];

const emit = defineEmits<{
  close: [];
  saved: [gameId: string];
  delete: [gameId: string];
}>();

const isEditing = computed(() => !!props.game);

const statuses: GameStatus[] = [
  "playing",
  "beaten",
  "mastered",
  "played",
  "on hold",
  "dropped",
  "backlog",
  "wishlist",
];

// One shared editor (#143): every section is a direct tab, adding and editing
// alike, and Save is always available. The lookup sits above any tab.
const TABS = [
  "Basics",
  "Details",
  "Play",
  "Ratings and tags",
  "Artwork",
  "Links",
  "Ownership",
  "Advanced",
] as const;
type Tab = (typeof TABS)[number];
const activeTab = ref<Tab>("Basics");
const metadataApplied = ref(false);
// The artwork tab is built the first time it is opened, then kept, so its
// file inputs, previews and offered images survive switching tabs.
const artworkOpened = ref(false);
watch(activeTab, (tab) => {
  if (tab === "Artwork") artworkOpened.value = true;
});
const shell = ref<{ scrollToTop: () => void } | null>(null);
const lookupId = useId();
const lookupOpen = ref(!props.game && !compact.value);
const lookupInput = ref<HTMLInputElement | null>(null);
function toggleLookup() {
  lookupOpen.value = !lookupOpen.value;
  if (!lookupOpen.value) return;
  shell.value?.scrollToTop();
  void nextTick(() => lookupInput.value?.focus());
}

const FOLDER_PATTERN = /^[A-Za-z0-9_-]+$/;
function validateBasics(): boolean {
  const folder = folderLocation.value.trim();
  if (!title.value.trim()) error.value = "Title is required.";
  else if (!isEditing.value && !folder)
    error.value = "Folder name is required.";
  else if (folder && !FOLDER_PATTERN.test(folder))
    error.value =
      "Folder name: use only letters, numbers, underscores and hyphens.";
  else return true;
  openFullForm();
  activeTab.value = "Basics";
  return false;
}

const title = ref(props.game?.title ?? "");
const { titleProtected, titleLockOverride } = useTitleProtection(
  () => props.game,
  () => title.value,
);

// the saved custom sorting name, blank when the library sorts by the title
const sortTitle = ref(props.game?.sortTitle ?? "");
const platform = ref(props.game?.platform ?? "");
const playCompatibility = ref<GamePlayCompatibility | null>(
  props.game?.playCompatibility ? { ...props.game.playCompatibility } : null,
);
const priority = ref(props.game?.priority ?? "");
const folderLocation = ref(props.game?.folderLocation ?? "");
const status = ref<GameStatus>(props.game?.status ?? "backlog");
const favorite = ref(props.game?.favorite ?? false);
const collections = ref<string[]>([...(props.game?.collections ?? [])]);
const developer = ref(props.game?.developer ?? "");
const publisher = ref(props.game?.publisher ?? "");
const series = ref(props.game?.series ?? "");
const parentGameId = ref(props.game?.parentGameId ?? "");
const relationshipType = ref<GameRelationshipType | "">(
  props.game?.relationshipType ?? "",
);
const RELATIONSHIP_TYPE_OPTIONS: {
  value: GameRelationshipType;
  label: string;
}[] = [
  { value: "mod", label: "Mod" },
  { value: "modpack", label: "Modpack" },
  { value: "expansion", label: "Expansion" },
  { value: "dlc", label: "DLC" },
  { value: "standalone_expansion", label: "Standalone expansion" },
  { value: "total_conversion", label: "Total conversion" },
  { value: "owned_copy", label: "Owned copy" },
];
const source = ref(props.game?.source ?? "");
const ageRating = ref(props.game?.ageRating ?? "");
// v-model on a type="number" input hands back a number once it's typed in,
// a string otherwise, so this must never assume either (calling .trim() on
// the number made every save with a typed-in time to beat throw)
const timeToBeatHours = ref<string | number>(
  props.game?.timeToBeatHours != null ? String(props.game.timeToBeatHours) : "",
);
const timeToBeatExtrasHours = ref<string | number>(
  props.game?.timeToBeatExtrasHours ?? "",
);
const timeToBeatCompletionistHours = ref<string | number>(
  props.game?.timeToBeatCompletionistHours ?? "",
);
const timeToBeatAllHours = ref<string | number>(
  props.game?.timeToBeatAllHours ?? "",
);
const completionEstimateInputs = [
  { label: "Main story", key: "timeToBeatHours", input: timeToBeatHours },
  {
    label: "Main + extras",
    key: "timeToBeatExtrasHours",
    input: timeToBeatExtrasHours,
  },
  {
    label: "Completionist",
    key: "timeToBeatCompletionistHours",
    input: timeToBeatCompletionistHours,
  },
  {
    label: "All play styles",
    key: "timeToBeatAllHours",
    input: timeToBeatAllHours,
  },
] as const;
// The manual playtime total, in hours to one decimal. It is only sent when
// changed, so opening and saving the editor never rounds a synced total.
const MAX_PLAYTIME_HOURS = 596523;
const savedPlaytimeMinutes = (props.game?.platforms ?? []).reduce(
  (sum, entry) => sum + entry.playtimeMinutes,
  0,
);
const initialPlaytime = savedPlaytimeMinutes
  ? String(Math.round(savedPlaytimeMinutes / 6) / 10)
  : "";
const playtimeHours = ref<string | number>(initialPlaytime);
const resumeNote = ref(props.game?.resumeNote ?? "");
const region = ref(props.game?.region ?? "");
const language = ref(props.game?.language ?? "");
const achievementsProvider = ref<AchievementsProvider>(
  props.game?.achievementsProvider ?? null,
);
const releaseDate = ref(props.game?.releaseDate ?? "");
// the local calendar day (what <input type="date"> shows), the stored value
// is a full timestamp; only sent back if it was actually changed, so saving
// the form doesn't reset the time a game was added to midnight
const initialDateAdded = toLocalDateInput(props.game?.dateAdded ?? new Date());
const dateAdded = ref(initialDateAdded);
const description = ref(props.game?.description ?? "");
const profilesEnabled = ref(props.game?.profilesEnabled ?? false);
// this game's overrides of what its page shows (see Settings > Game Page)
const pageOverrides = ref<PageOverrides>(
  JSON.parse(JSON.stringify(props.game?.pageSettings ?? {})),
);
const pageDefaults = computed<PageSettings>(() =>
  resolvePage(preferences.value.game_page, null),
);
const osrsStatsEnabled = ref(props.game?.osrsStatsEnabled ?? false);
watch(profilesEnabled, (enabled) => {
  if (!enabled) osrsStatsEnabled.value = false;
});

const ratingOverall = ref<number | null>(props.game?.ratingOverall ?? null);
const ratingStory = ref<number | null>(props.game?.ratingStory ?? null);
const ratingGameplay = ref<number | null>(props.game?.ratingGameplay ?? null);
const ratingSound = ref<number | null>(props.game?.ratingSound ?? null);
const tagsInput = ref(props.game?.tags.join(", ") ?? "");
const featuresInput = ref(props.game?.features.join(", ") ?? "");

// Pending artwork per role. A chosen file, link or offered image is staged
// here (with its own object URL) until Save; the saved artwork stays shown.
const cover = useStagedArtwork(() => props.game?.coverImageUrl ?? null);
const banner = useStagedArtwork(() =>
  props.game ? sizedAssetUrl(props.game.bannerImageUrl, 640) : null,
);

const links = ref<GameLink[]>(props.game?.links ? [...props.game.links] : []);
function addLink() {
  links.value.push({ label: "", url: "" });
}
function removeLink(index: number) {
  links.value.splice(index, 1);
}

const ownershipFormat = ref<GameOwnership["format"]>(
  props.game?.ownership.format ?? null,
);
const purchaseDate = ref(props.game?.ownership.purchaseDate ?? "");
const completionDate = ref(props.game?.completionDate ?? "");
const price = ref<number | null>(props.game?.ownership.price ?? null);
const priceCurrency = ref(props.game?.ownership.priceCurrency ?? "USD");
const condition = ref(props.game?.ownership.condition ?? "");

const heroTitle = computed(() => title.value.trim() || "Untitled game");
const heroSubtitle = computed(() =>
  [developer.value.trim(), releaseDate.value.slice(0, 4), platform.value.trim()]
    .filter(Boolean)
    .join(" · "),
);
const dialogTitle = computed(() =>
  isEditing.value ? "Edit Game" : compact.value ? "Quick Add Game" : "Add Game",
);

const saving = ref(false);
const error = ref<string | null>(null);
const metadataQuery = ref("");
const progressiveSearch = useMetadataSearch(metadataQuery, "game");
const selectingMetadata = ref(false);
const selectedProviderIds = ref<Record<string, string> | undefined>();
let selectionGeneration = 0;
const metadataResults = computed(() =>
  progressiveSearch.selected.value || metadataApplied.value
    ? []
    : progressiveSearch.results.value.map(gameMetadataResult),
);
const searchingMetadata = progressiveSearch.searching;
const metadataMessage = ref<string | null>(null);
const appliedNote = ref<string | null>(null);
const metadataProviders = ref<MetadataProviderStatus[]>([]);
const metadataProvidersLoaded = ref(false);
const providerWarnings = progressiveSearch.warnings;

// offered by the selected metadata result; choosing one only stages it
const keyArtCandidates = ref<string[]>([]);
const bannerCandidates = ref<string[]>([]);
const searchingMedia = progressiveSearch.enrichingMedia;

const hasMediaProvider = computed(() =>
  metadataProviders.value.some(
    (provider) =>
      provider.media_types.includes("game") &&
      provider.operations.media &&
      !["disabled", "not_configured", "plugin_unavailable"].includes(
        provider.state,
      ),
  ),
);

async function searchMetadata() {
  metadataApplied.value = false;
  appliedNote.value = null;
  await progressiveSearch.search();
}

async function searchMedia() {
  const query = title.value.trim();
  if (query.length < 2) {
    metadataMessage.value = "Enter a game title before searching for artwork.";
    return;
  }
  try {
    await progressiveSearch.focus(
      query,
      selectedProviderIds.value ?? props.game?.providerIds ?? {},
    );
  } catch (failure) {
    metadataMessage.value =
      failure instanceof Error ? failure.message : "Media search failed.";
  }
}

watch(
  [
    progressiveSearch.error,
    searchingMetadata,
    metadataResults,
    progressiveSearch.manualSearchHint,
    progressiveSearch.hasSearched,
  ],
  () => {
    if (progressiveSearch.error.value)
      metadataMessage.value = progressiveSearch.error.value;
    else if (progressiveSearch.selected.value)
      metadataMessage.value = "Review the offered fields before applying them.";
    else if (!metadataApplied.value)
      metadataMessage.value =
        progressiveSearch.manualSearchHint.value ??
        (searchingMetadata.value
          ? null
          : progressiveSearch.hasSearched.value && !metadataResults.value.length
            ? "No games found."
            : null);
  },
);

const artwork = useMetadataArtwork(
  progressiveSearch.selected,
  progressiveSearch.previewArtwork,
);
// The comparison reads and stages artwork through the same choices as the
// Artwork tab: ticking an offered image is an explicit choice, while a staged
// local file counts as a current value, so it is not ticked by default.
const lookupCover = computed({
  get: () => cover.preview.value,
  set: (url: string | null) =>
    url ? cover.chooseUrl(url, "provider") : cover.reset(),
});
const lookupBanner = computed({
  get: () => banner.preview.value,
  set: (url: string | null) =>
    url ? banner.chooseUrl(url, "provider") : banner.reset(),
});
const lookupIds = computed({
  get: () => selectedProviderIds.value ?? props.game?.providerIds ?? {},
  set: (ids: Record<string, string>) => {
    selectedProviderIds.value = ids;
  },
});
const lookupFields = proxyRefs({
  title,
  description,
  developer,
  publisher,
  series,
  source,
  age_rating: ageRating,
  release_date: releaseDate,
  tags: tagsInput,
  features: featuresInput,
  time_to_beat_hours: timeToBeatHours,
  time_to_beat_extras_hours: timeToBeatExtrasHours,
  time_to_beat_completionist_hours: timeToBeatCompletionistHours,
  time_to_beat_all_hours: timeToBeatAllHours,
  links,
  provider_ids: lookupIds,
  key_art: lookupCover,
  banner: lookupBanner,
});
const lookupLabels = {
  title: "Title",
  description: "Description",
  developer: "Developer",
  publisher: "Publisher",
  series: "Series",
  source: "Source",
  age_rating: "Age rating",
  release_date: "Release date",
  tags: "Tags",
  features: "Features",
  time_to_beat_hours: "Main story",
  time_to_beat_extras_hours: "Main story and extras",
  time_to_beat_completionist_hours: "Completionist",
  time_to_beat_all_hours: "All play styles",
  links: "Links",
  provider_ids: "Provider identity",
  key_art: "Cover",
  banner: "Banner",
};
const comparison = useMetadataComparison(
  () => lookupFields,
  () =>
    Object.fromEntries(
      Object.entries(lookupLabels).map(([key, label]) => [
        key,
        {
          label,
          locked:
            key === "title"
              ? titleProtected.value
              : (props.game?.lockedFields ?? []).includes(key),
          image: key === "key_art" || key === "banner",
        },
      ]),
    ),
);
watch([progressiveSearch.selected, artwork.urls], ([candidate]) => {
  if (!candidate) {
    comparison.clear();
    return;
  }
  const result = gameMetadataResult(candidate);
  const hours = (value: number | string | null | undefined) =>
    value == null ? undefined : String(value);
  comparison.offer(
    {
      title: result.title,
      description: result.description ?? undefined,
      developer: result.developer ?? undefined,
      publisher: result.publisher ?? undefined,
      series: result.series ?? undefined,
      source: result.provider,
      age_rating: result.age_rating ?? undefined,
      release_date: result.release_date ?? undefined,
      tags: result.tags.join(", "),
      features: result.features.join(", "),
      links: result.links,
      time_to_beat_hours: hours(result.time_to_beat_hours),
      time_to_beat_extras_hours: hours(result.time_to_beat_extras_hours),
      time_to_beat_completionist_hours: hours(
        result.time_to_beat_completionist_hours,
      ),
      time_to_beat_all_hours: hours(result.time_to_beat_all_hours),
      provider_ids: { ...lookupIds.value, ...candidate.provider_ids },
      key_art: artwork.urls.value.cover,
      banner: artwork.urls.value.banner,
    },
    candidate.id,
  );
  keyArtCandidates.value = result.key_art_urls;
  bannerCandidates.value = result.banner_urls;
});
// A new review opens at the top of the body, whichever tab is showing.
watch(progressiveSearch.selected, (candidate, previous) => {
  if (candidate && !previous) shell.value?.scrollToTop();
});
function cancelLookup() {
  comparison.clear();
  progressiveSearch.stop();
}
function applyLookup() {
  const candidate = progressiveSearch.selected.value;
  if (!candidate) return;
  const applied = comparison.apply();
  if (!isEditing.value && applied.has("title")) suggestFolderFromTitle();
  metadataApplied.value = true;
  metadataMessage.value = `Applied ${applied.size} changes from ${candidate.provider_name}. Save to keep them.`;
  progressiveSearch.stop();
  if (!compact.value) {
    appliedNote.value = metadataMessage.value;
    lookupOpen.value = false;
  }
}
async function applyMetadata(result: MetadataSearchResult) {
  if (saving.value) return;
  const candidate = progressiveSearch.results.value.find(
    (item) => item.id === result.candidate_id,
  );
  if (!candidate) {
    metadataMessage.value = "Search for this title again before selecting it.";
    return;
  }
  const request = ++selectionGeneration;
  selectingMetadata.value = true;
  try {
    await progressiveSearch.select(candidate);
  } catch {
    /* Available partial fields remain reviewable after an enrichment failure. */
  } finally {
    if (request === selectionGeneration) selectingMetadata.value = false;
  }
}
watch(() => props.game, cancelLookup);

// when editing, the folder name is already real data, don't let the
// title-blur auto-suggest silently overwrite it
const folderTouched = ref(isEditing.value);
function suggestFolderFromTitle() {
  if (folderTouched.value) return;
  folderLocation.value = title.value
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function buildInput() {
  const purchasePrice = numberOrNull(price.value, "Purchase price", {
    min: 0,
  });
  const playtimeChanged =
    String(playtimeHours.value).trim() !== initialPlaytime;
  const playtime = playtimeChanged
    ? (numberOrNull(playtimeHours.value, "Playtime", {
        min: 0,
        max: MAX_PLAYTIME_HOURS,
      }) ?? 0)
    : null;
  return {
    title: title.value.trim(),
    titleLock: isEditing.value ? titleLockOverride.value : undefined,
    sortTitle: sortTitle.value.trim() || null,
    folderLocation: folderLocation.value.trim(),
    status: status.value,
    description: description.value.trim() || null,
    developer: developer.value.trim() || null,
    publisher: publisher.value.trim() || null,
    series: series.value.trim() || null,
    parentGameId: parentGameId.value || null,
    relationshipType: parentGameId.value
      ? relationshipType.value || null
      : null,
    releaseDate: releaseDate.value || null,
    dateAdded: dateAdded.value || null,
    createdAt:
      dateAdded.value && dateAdded.value !== initialDateAdded
        ? localDateInputToUnixSeconds(dateAdded.value)
        : null,
    completionDate: completionDate.value || null,
    source: source.value.trim() || null,
    providerIds: selectedProviderIds.value ?? props.game?.providerIds,
    platform: platform.value.trim() || null,
    playCompatibility: playCompatibility.value,
    priority: priority.value || null,
    ageRating: ageRating.value.trim() || null,
    timeToBeatHours: numberOrNull(timeToBeatHours.value, "Time to beat", {
      min: 0,
      max: 99999.9,
    }),
    timeToBeatExtrasHours: numberOrNull(
      timeToBeatExtrasHours.value,
      "Main + extras",
      { min: 0, max: 99999.9 },
    ),
    timeToBeatCompletionistHours: numberOrNull(
      timeToBeatCompletionistHours.value,
      "Completionist",
      { min: 0, max: 99999.9 },
    ),
    timeToBeatAllHours: numberOrNull(
      timeToBeatAllHours.value,
      "All play styles",
      { min: 0, max: 99999.9 },
    ),
    region: region.value.trim() || null,
    language: language.value.trim() || null,
    achievementsProvider: achievementsProvider.value,
    ratingOverall: numberOrNull(ratingOverall.value, "Overall rating", {
      min: 0,
      max: 10,
    }),
    ratingStory: numberOrNull(ratingStory.value, "Story rating", {
      min: 0,
      max: 10,
    }),
    ratingGameplay: numberOrNull(ratingGameplay.value, "Gameplay rating", {
      min: 0,
      max: 10,
    }),
    ratingSound: numberOrNull(ratingSound.value, "Soundtrack rating", {
      min: 0,
      max: 10,
    }),
    tags: tagsInput.value
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    features: featuresInput.value
      .split(",")
      .map((f) => f.trim())
      .filter(Boolean),
    links: links.value.filter((l) => l.label.trim() && l.url.trim()),
    ownership: {
      format: ownershipFormat.value,
      purchaseDate: purchaseDate.value || null,
      price: purchasePrice,
      priceCurrency:
        purchasePrice !== null
          ? priceCurrency.value.trim().toUpperCase() || "USD"
          : null,
      condition: condition.value.trim() || null,
    },
    // Tracking fields travel in the same validated game write as the rest,
    // so they cannot be saved without the game or the game without them.
    favorite: favorite.value,
    collections: collections.value,
    resumeNote: resumeNote.value.trim() || null,
    playtimeSeconds:
      playtime === null ? undefined : Math.round(playtime * 3600),
    profilesEnabled: profilesEnabled.value,
    pageSettings: pageOverrides.value,
    osrsStatsEnabled: osrsStatsEnabled.value,
  };
}

// The staged artwork, captured when Save starts. An explicit local file is
// uploaded; a link or offered image is fetched by the server.
function stagedArtwork() {
  return [
    { kind: "key_art" as const, label: "cover", choice: cover.staged.value },
    { kind: "banner" as const, label: "banner", choice: banner.staged.value },
  ].flatMap(({ kind, label, choice }) =>
    choice
      ? [
          choice.source === "file"
            ? { kind, label, file: choice.file, url: null }
            : { kind, label, file: null, url: choice.url },
        ]
      : [],
  );
}

// Artwork is transferred after the game is saved. A failure leaves the game
// saved and offers a retry of the remaining artwork on that same game only.
async function attachArtwork(
  savedGame: Game,
  artwork: ReturnType<typeof stagedArtwork>,
) {
  const taskId = startTask(`Artwork for ${savedGame.title}`, artwork.length);
  let done = 0;
  let running = false;
  const attempt = async () => {
    if (running || done === artwork.length) return;
    running = true;
    try {
      while (done < artwork.length) {
        const asset = artwork[done]!;
        if (asset.file)
          await uploadGameAsset(savedGame.id, asset.kind, asset.file);
        else if (asset.url)
          await attachGameAssetFromUrl(savedGame.id, asset.kind, asset.url);
        updateTask(taskId, ++done);
      }
      completeTask(taskId, "Artwork saved.");
    } catch {
      errorTask(
        taskId,
        `Game saved. Artwork could not be attached (${artwork[done]!.label}). Retry artwork or edit it later.`,
      );
      setTaskRetry(taskId, () => void attempt());
    } finally {
      running = false;
    }
  };
  await attempt();
}

// A game created by this editor; a later Save updates it rather than
// creating another copy.
let createdGame: Game | null = null;

async function submit() {
  if (progressiveSearch.selected.value) return;
  if (saving.value || selectingMetadata.value) return;
  if (compact.value) suggestFolderFromTitle();
  if (!validateBasics()) return;

  saving.value = true;
  error.value = null;

  try {
    const input = buildInput();
    // Capture artwork before awaiting the save. Retries only attach remaining assets
    // to this saved game, even after the dialog has closed or another one is opened.
    const pendingArtwork = stagedArtwork();
    const targetId = createdGame?.id ?? props.game?.id;
    const savedGame = targetId
      ? await updateGame(targetId, input)
      : await createGame(input);
    if (!props.game) createdGame = savedGame;

    if (import.meta.env.VITE_USE_MOCK_DATA !== "true" && pendingArtwork.length)
      await attachArtwork(savedGame, pendingArtwork);

    emit("saved", savedGame.id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to save game";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <EditorShell
    ref="shell"
    v-model:tab="activeTab"
    :title="dialogTitle"
    :heading="compact ? undefined : heroTitle"
    :subtitle="heroSubtitle"
    :cover="cover.preview.value"
    cover-label="Show artwork"
    :tabs="compact ? [] : TABS"
    tabs-label="Game details"
    :size="compact ? 'standard' : 'editor'"
    :dismissible="!saving"
    class="game-editor"
    :class="{ 'quick-add': compact }"
    @close="emit('close')"
    @submit="submit"
    @cover="activeTab = 'Artwork'"
  >
    <template #lead>
      <p
        v-if="!compact && !lookupOpen && appliedNote"
        class="lookup-note"
        role="status"
      >
        {{ appliedNote }}
      </p>
      <MetadataComparison
        v-if="progressiveSearch.selected.value"
        :changes="comparison.changes.value"
        :reason="comparison.reason.value"
        :provider="progressiveSearch.selected.value.provider_name"
        :busy="artwork.busy.value || searchingMedia"
        :previews="artwork.previews.value"
        @toggle="comparison.toggle"
        @apply="applyLookup"
        @cancel="cancelLookup"
      />
      <div v-if="compact || lookupOpen" :id="lookupId" class="metadata-search">
        <div class="search-heading">
          <strong>Find game metadata</strong>
          <span
            >Search external providers and choose a match to review its
            fields.</span
          >
        </div>
        <p
          v-if="metadataProvidersLoaded && !hasMediaProvider"
          class="steamgriddb-hint"
        >
          Configure the built-in artwork providers in
          <router-link to="/settings?section=sources" @click="emit('close')"
            >Settings &rsaquo; Metadata/API</router-link
          >
          to receive cover and hero artwork after choosing a game.
        </p>
        <div class="search-row">
          <input
            ref="lookupInput"
            v-model="metadataQuery"
            type="search"
            placeholder="Search by game title"
            aria-label="Search game metadata by title"
            @keydown.enter.prevent="searchMetadata"
          />
          <button
            type="button"
            class="secondary-button"
            :disabled="searchingMetadata"
            @click="searchMetadata"
          >
            {{ searchingMetadata ? "Searching…" : "Search" }}
          </button>
        </div>
        <div v-if="metadataResults.length" class="metadata-results">
          <button
            v-for="result in metadataResults"
            :key="`${result.provider}-${result.provider_id}`"
            type="button"
            class="metadata-result"
            @click="applyMetadata(result)"
          >
            <span>{{ result.title }}</span>
            <small
              >{{ result.provider
              }}<span v-if="result.release_date">
                · {{ result.release_date.slice(0, 4) }}</span
              ></small
            >
          </button>
        </div>
        <p v-if="metadataMessage" class="hint">{{ metadataMessage }}</p>
        <ul v-if="providerWarnings.length" class="provider-warnings">
          <li v-for="warning in providerWarnings" :key="warning">
            {{ warning }}
          </li>
        </ul>
        <p v-if="!compact && !isEditing" class="hint">
          Pick a match to review what it fills in, or enter everything by hand
          in the tabs below.
        </p>
      </div>
    </template>

    <template v-if="compact">
      <div class="editor-grid">
        <label class="field full">
          <span>Title</span>
          <input
            v-model="title"
            type="text"
            required
            @blur="suggestFolderFromTitle"
          />
        </label>
        <label class="field">
          <span>Status</span>
          <select v-model="status">
            <option v-for="s in statuses" :key="s" :value="s">
              {{ s }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>Platform</span>
          <input
            v-model="platform"
            type="text"
            list="quick-game-platforms"
            placeholder="PC, Switch..."
          />
          <datalist id="quick-game-platforms">
            <option
              v-for="option in PLATFORM_SUGGESTIONS"
              :key="option"
              :value="option"
            />
          </datalist>
        </label>
      </div>
      <p class="hint">
        Choose a search result or enter a title. Use the full form for artwork,
        ownership and more fields.
      </p>
    </template>

    <template v-else>
      <div v-if="activeTab === 'Basics'" class="tab-panel">
        <p class="editor-section">Identity</p>
        <div class="editor-grid">
          <label class="field full">
            <span>Title</span>
            <input
              v-model="title"
              type="text"
              required
              @blur="suggestFolderFromTitle"
            />
          </label>
          <label v-if="isEditing" class="checkbox-field full">
            <input v-model="titleProtected" type="checkbox" />
            <span>Protect title from metadata updates</span>
          </label>
          <label class="field">
            <span>Sorting name</span>
            <input
              v-model="sortTitle"
              type="text"
              placeholder="Defaults to the title"
            />
          </label>
          <label class="field">
            <span>Folder name</span>
            <input
              v-model="folderLocation"
              type="text"
              :required="!isEditing"
              pattern="[A-Za-z0-9_\-]+"
              title="Letters, numbers, underscores and hyphens only"
              :placeholder="isEditing ? 'Leave blank to keep current' : ''"
              @input="folderTouched = true"
            />
          </label>
        </div>
        <p class="editor-section">Library</p>
        <div class="editor-grid">
          <label class="field">
            <span>Status</span>
            <select v-model="status">
              <option v-for="s in statuses" :key="s" :value="s">
                {{ s }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Priority</span>
            <select v-model="priority">
              <option value="">None</option>
              <option
                v-for="option in PRIORITY_OPTIONS"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
            <small v-if="priority && isFinished(status)" class="field-hint"
              >Finished games are left out of priority sorting and the random
              picker.</small
            >
          </label>
          <label class="field">
            <span>Platform</span>
            <input
              v-model="platform"
              type="text"
              list="game-platform-suggestions"
              placeholder="PC, PlayStation 5, Switch..."
            />
            <datalist id="game-platform-suggestions">
              <option
                v-for="option in PLATFORM_SUGGESTIONS"
                :key="option"
                :value="option"
              />
            </datalist>
          </label>
          <label class="field">
            <span>Source</span>
            <input
              v-model="source"
              type="text"
              placeholder="Steam, GOG, physical..."
            />
          </label>
          <label class="field">
            <span>Date added to library</span>
            <input v-model="dateAdded" type="date" />
          </label>
          <label class="checkbox-field favorite-field">
            <input v-model="favorite" type="checkbox" />
            <span>
              Favorite
              <small>Shown in the Favorites collection.</small>
            </span>
          </label>
        </div>
      </div>

      <div v-else-if="activeTab === 'Details'" class="tab-panel">
        <p class="editor-section">About</p>
        <div class="editor-grid">
          <label class="field full">
            <span>Description</span>
            <textarea v-model="description" rows="5"></textarea>
          </label>
          <label class="field">
            <span>Developer</span>
            <input v-model="developer" type="text" />
          </label>
          <label class="field">
            <span>Publisher</span>
            <input v-model="publisher" type="text" />
          </label>
          <label class="field">
            <span>Series</span>
            <input v-model="series" type="text" />
          </label>
          <label class="field">
            <span>Release date</span>
            <input v-model="releaseDate" type="date" />
          </label>
        </div>
        <p class="editor-section">Release details</p>
        <div class="editor-grid">
          <label class="field">
            <span>Age rating</span>
            <input
              v-model="ageRating"
              type="text"
              placeholder="ESRB M, PEGI 18..."
            />
          </label>
          <label class="field">
            <span>Region</span>
            <input v-model="region" type="text" placeholder="NA, PAL, JP..." />
          </label>
          <label class="field">
            <span>Language</span>
            <input
              v-model="language"
              type="text"
              placeholder="English, Japanese..."
            />
          </label>
        </div>
      </div>

      <div v-else-if="activeTab === 'Play'" class="tab-panel">
        <p class="editor-section">Progress</p>
        <div class="editor-grid">
          <label class="field">
            <span>Playtime (hours)</span>
            <input
              v-model="playtimeHours"
              type="number"
              min="0"
              :max="MAX_PLAYTIME_HOURS"
              step="0.1"
              placeholder="0"
            />
          </label>
          <label class="field">
            <span>100% completion date</span>
            <input v-model="completionDate" type="date" />
          </label>
          <label class="field full">
            <span>Where you left off</span>
            <textarea
              v-model="resumeNote"
              rows="3"
              maxlength="2000"
              placeholder="What to do when you pick this game back up"
            ></textarea>
          </label>
        </div>
        <p class="hint">
          Your own playtime total. A connected library sync can replace it with
          the store's total.
        </p>
        <p class="editor-section">Time to beat (hours)</p>
        <div class="editor-grid four">
          <label
            v-for="estimate in completionEstimateInputs"
            :key="estimate.key"
            class="field"
          >
            <span>{{ estimate.label }}</span>
            <input
              v-model="estimate.input.value"
              type="number"
              min="0"
              max="99999.9"
              step="0.1"
              placeholder="e.g. 12.5"
            />
          </label>
        </div>
        <p class="hint">
          Community estimates; they never add to your playtime.
        </p>
        <GamePlayFields v-model="playCompatibility" />
      </div>

      <div v-else-if="activeTab === 'Ratings and tags'" class="tab-panel">
        <p class="editor-section">Your ratings (0 to 10)</p>
        <div class="editor-grid four">
          <label class="field">
            <span>Atmosphere</span>
            <input
              v-model.number="ratingOverall"
              type="number"
              min="0"
              max="10"
              step="0.1"
            />
          </label>
          <label class="field">
            <span>Story</span>
            <input
              v-model.number="ratingStory"
              type="number"
              min="0"
              max="10"
              step="0.1"
            />
          </label>
          <label class="field">
            <span>Gameplay</span>
            <input
              v-model.number="ratingGameplay"
              type="number"
              min="0"
              max="10"
              step="0.1"
            />
          </label>
          <label class="field">
            <span>Sound</span>
            <input
              v-model.number="ratingSound"
              type="number"
              min="0"
              max="10"
              step="0.1"
            />
          </label>
        </div>
        <p class="editor-section">Organize</p>
        <div class="editor-grid">
          <label class="field full">
            <span>Tags (comma separated)</span>
            <input
              v-model="tagsInput"
              type="text"
              placeholder="Action RPG, Souls-Like"
            />
          </label>
          <label class="field full">
            <span>Features (comma separated)</span>
            <input
              v-model="featuresInput"
              type="text"
              placeholder="Achievements, Cloud Saves"
            />
          </label>
          <GameCollectionsField
            v-model="collections"
            class="full"
            :available="collectionChoices"
          />
        </div>
      </div>

      <div v-else-if="activeTab === 'Links'" class="tab-panel">
        <p class="editor-section">Links</p>
        <div
          v-for="(link, index) in links"
          :key="index"
          class="editor-grid link-row"
        >
          <label class="field">
            <span>Label</span>
            <input
              v-model="link.label"
              type="text"
              placeholder="Steam Store Page"
            />
          </label>
          <label class="field">
            <span>URL</span>
            <input v-model="link.url" type="url" placeholder="https://..." />
          </label>
          <button
            type="button"
            class="remove-button"
            :aria-label="`Remove link ${index + 1}`"
            @click="removeLink(index)"
          >
            ✕
          </button>
        </div>
        <p v-if="!links.length" class="hint">No links yet.</p>
        <button type="button" class="secondary-button" @click="addLink">
          + Add Link
        </button>
      </div>

      <div v-else-if="activeTab === 'Ownership'" class="tab-panel">
        <p class="editor-section">Purchase</p>
        <div class="editor-grid">
          <label class="field">
            <span>Format</span>
            <select v-model="ownershipFormat">
              <option :value="null">Unspecified</option>
              <option value="digital">Digital</option>
              <option value="physical">Physical</option>
            </select>
          </label>
          <label class="field">
            <span>Purchase date</span>
            <input v-model="purchaseDate" type="date" />
          </label>
          <label class="field">
            <span>Price</span>
            <input v-model.number="price" type="number" min="0" step="0.01" />
          </label>
          <label class="field">
            <span>Currency</span>
            <select v-if="currencyCodes.length" v-model="priceCurrency">
              <option v-for="code in currencyCodes" :key="code" :value="code">
                {{ code }}
              </option>
            </select>
            <input
              v-else
              v-model="priceCurrency"
              type="text"
              placeholder="USD"
              maxlength="3"
            />
          </label>
          <label v-if="ownershipFormat === 'physical'" class="field full">
            <span>Condition / notes</span>
            <input
              v-model="condition"
              type="text"
              placeholder="CIB, disc only, box wear..."
            />
          </label>
        </div>
      </div>

      <div v-else-if="activeTab === 'Advanced'" class="tab-panel">
        <p class="editor-section">Related games</p>
        <div class="editor-grid">
          <label class="field">
            <span>Parent game</span>
            <select v-model="parentGameId">
              <option value="">None: this is its own game</option>
              <option
                v-for="g in availableParentGames"
                :key="g.id"
                :value="g.id"
              >
                {{ g.title }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Relationship</span>
            <select v-model="relationshipType" :disabled="!parentGameId">
              <option value="">N/A</option>
              <option
                v-for="opt in RELATIONSHIP_TYPE_OPTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
            <small v-if="!parentGameId" class="field-hint"
              >Choose a parent game first.</small
            >
          </label>
        </div>
        <p class="editor-section">Accounts</p>
        <label class="checkbox-field">
          <input v-model="profilesEnabled" type="checkbox" />
          <span>
            Track multiple accounts on this game
            <small
              >Adds an account switcher with its own checklist and media for
              each account, useful for any game with multiple
              characters/accounts, not just OSRS.</small
            >
          </span>
        </label>
        <label v-if="profilesEnabled" class="checkbox-field">
          <input v-model="osrsStatsEnabled" type="checkbox" />
          <span>
            Use OSRS stats (WiseOldMan)
            <small
              >Adds skill/boss syncing from wiseoldman.net, real skill icons,
              and dated stat history to each account. Only makes sense for Old
              School RuneScape.</small
            >
          </span>
        </label>
        <p class="editor-section">Game page</p>
        <p class="hint page-hint">
          What this game's page shows. Anything left on Default follows Settings
          > Game Page.
        </p>
        <PageSettingsEditor
          :model-value="pageOverrides"
          :defaults="pageDefaults"
          @update:model-value="pageOverrides = $event as PageOverrides"
        />
      </div>

      <div
        v-if="artworkOpened"
        v-show="activeTab === 'Artwork'"
        class="tab-panel"
      >
        <div class="media-search-panel">
          <div>
            <strong>Find artwork</strong>
            <p class="hint">
              Search the configured artwork sources for cover and banner
              choices. Offered images never replace a file you chose.
            </p>
          </div>
          <button
            type="button"
            class="secondary-button"
            :disabled="searchingMedia || saving"
            @click="searchMedia"
          >
            {{ searchingMedia ? "Searching artwork…" : "Search artwork" }}
          </button>
          <p v-if="metadataMessage" class="hint" role="status">
            {{ metadataMessage }}
          </p>
        </div>
        <GameArtPicker
          label="Cover"
          shape="tall"
          :current="props.game?.coverImageUrl ?? null"
          :staged="cover.staged.value"
          :candidates="keyArtCandidates"
          :disabled="saving"
          @file="cover.chooseFile"
          @url="cover.chooseUrl"
          @reset="cover.reset"
        />
        <GameArtPicker
          label="Banner"
          shape="wide"
          :current="
            props.game ? sizedAssetUrl(props.game.bannerImageUrl, 640) : null
          "
          :staged="banner.staged.value"
          :candidates="bannerCandidates"
          :disabled="saving"
          @file="banner.chooseFile"
          @url="banner.chooseUrl"
          @reset="banner.reset"
        />
        <p class="hint">
          Nothing is uploaded until you click Add Game or Save Changes. If the
          game saves but artwork cannot be attached, the game stays saved and
          you can retry the artwork.
        </p>
      </div>
    </template>

    <template v-if="error" #status>
      <div class="form-error" role="alert">{{ error }}</div>
    </template>

    <template #actions="{ formId }">
      <button
        v-if="isEditing"
        type="button"
        class="danger-button"
        @click="emit('delete', props.game!.id)"
      >
        Delete Game
      </button>
      <button
        v-if="compact"
        type="button"
        class="secondary-button"
        :disabled="saving"
        @click="openFullForm"
      >
        Full form
      </button>
      <button
        v-else
        type="button"
        class="secondary-button"
        :aria-expanded="lookupOpen"
        :aria-controls="lookupId"
        @click="toggleLookup"
      >
        Look up metadata
      </button>
      <span class="modal-actions-spacer"></span>
      <button
        type="button"
        class="secondary-button"
        :disabled="saving"
        @click="emit('close')"
      >
        Cancel
      </button>
      <button
        type="submit"
        :form="formId"
        class="primary-button"
        :disabled="
          saving ||
          selectingMetadata ||
          !!progressiveSearch.selected.value ||
          (compact && !title.trim())
        "
      >
        {{ saving ? "Saving…" : isEditing ? "Save Changes" : "Add Game" }}
      </button>
    </template>
  </EditorShell>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped src="../styles/shared/editorActions.css"></style>
<style scoped>
.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.tab-panel > .editor-section {
  margin-bottom: -4px;
}
.tab-panel > .editor-section:not(:first-child) {
  margin-top: 8px;
}
.favorite-field {
  align-self: end;
}
.page-hint {
  margin-bottom: 4px;
}
.lookup-note {
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--ui-accent-line);
  border-radius: var(--ui-radius-control);
  background: var(--ui-accent-soft);
  color: var(--ui-text);
  font-size: 0.82rem;
}
.media-search-panel {
  flex-wrap: wrap;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 12px;
  background: var(--ui-surface-2);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.media-search-panel > div {
  min-width: 0;
}
.media-search-panel strong {
  color: var(--ui-text);
}
.media-search-panel .hint {
  margin: 2px 0 0;
}
.metadata-search {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 12px;
  background: var(--ui-surface);
}
.search-heading {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-bottom: 10px;
}
.search-heading span,
.metadata-result small {
  color: var(--ui-dim);
  font-size: 0.78rem;
}
.steamgriddb-hint {
  margin: 0 0 10px;
  padding: 8px 10px;
  background: color-mix(in srgb, var(--ui-accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--ui-accent) 30%, transparent);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  font-size: 0.78rem;
  line-height: 1.5;
}
.steamgriddb-hint a {
  color: var(--ui-accent-text);
  font-weight: 600;
  text-decoration: none;
}
.steamgriddb-hint a:hover {
  text-decoration: underline;
}
.search-row {
  display: flex;
  gap: 8px;
}
.search-row input {
  flex: 1;
  min-width: 0;
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 9px 11px;
  font: inherit;
}
.search-row input:focus {
  outline: none;
  border-color: var(--ui-accent-line);
}
.metadata-results {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}
.metadata-result {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  width: 100%;
  padding: 9px 10px;
  text-align: left;
  color: var(--ui-text);
  background: var(--ui-border);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  cursor: pointer;
}
.metadata-result:hover {
  border-color: var(--ui-accent-line);
  background: var(--ui-surface-2);
}
.metadata-search .hint {
  margin-top: 8px;
}
.provider-warnings {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.provider-warnings li {
  color: var(--ui-warning);
  font-size: 0.78rem;
}
.link-row {
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  align-items: end;
}
.remove-button {
  background: color-mix(in srgb, var(--ui-error) 15%, transparent);
  color: var(--ui-error);
  border: none;
  border-radius: var(--ui-radius-control);
  width: var(--ui-control-height);
  height: var(--ui-control-height);
  cursor: pointer;
  transition: background 0.15s ease;
}
.remove-button:hover {
  background: color-mix(in srgb, var(--ui-error) 30%, transparent);
}
.tab-panel > .secondary-button {
  align-self: flex-start;
}
.form-error {
  color: var(--ui-error);
  font-size: 0.85rem;
  background: color-mix(in srgb, var(--ui-error) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--ui-error) 30%, transparent);
  border-radius: var(--ui-radius-control);
  padding: 10px 12px;
}
.quick-add .form-error {
  margin-top: 14px;
}
.modal-actions-spacer {
  flex: 1;
}
.danger-button {
  background: color-mix(in srgb, var(--ui-error) 15%, transparent);
  color: var(--ui-error);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 10px 20px;
  font-weight: 600;
  font-size: 0.9rem;
  white-space: nowrap;
  cursor: pointer;
  transition: background 0.15s ease;
}
.danger-button:hover {
  background: color-mix(in srgb, var(--ui-error) 30%, transparent);
}
.primary-button,
.secondary-button {
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 10px 20px;
  font-weight: 600;
  font-size: 0.9rem;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background 0.15s ease,
    transform 0.05s ease;
}
.remove-button:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}
.secondary-button[aria-expanded="true"] {
  background: var(--ui-accent-soft);
  color: var(--ui-accent-text);
}
:is(
  .primary-button,
  .secondary-button,
  .danger-button,
  .remove-button
):focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}
@media (max-width: 480px) {
  .link-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .link-row > .field:nth-child(2) {
    grid-column: 1 / -1;
    order: 3;
  }
  .primary-button,
  .secondary-button,
  .danger-button {
    padding: 10px 14px;
  }
}
</style>
