<script setup lang="ts">
import { useConfirm } from "../../state/dialog";
import { ref, computed, onMounted } from "vue";
import SegmentedControl from "./SegmentedControl.vue";
import { fetchPreferences, queuePreferences } from "../../services/preferences";
import { preferences as sharedPreferences } from "../../state/preferences";
import type { EmptyTrashResult } from "../../services/mediaApi";
import {
  fetchMovieTrash,
  restoreMovie,
  purgeMovie,
  emptyMovieTrash,
} from "../../services/movies";
import type { TrashedMovie } from "../../services/movies";
import {
  fetchTVShowTrash,
  restoreTVShow,
  purgeTVShow,
  emptyTVShowTrash,
} from "../../services/tvShows";
import type { TrashedTVShow } from "../../services/tvShows";
import {
  fetchAnimeTrash,
  restoreAnime,
  purgeAnime,
  emptyAnimeTrash,
} from "../../services/anime";
import type { TrashedAnime } from "../../services/anime";
import {
  RETENTION_CHOICES,
  countOf,
  emptyTrashMessage,
  expiredUnder,
  newestDeletion,
  retentionDeadline,
} from "../../utils/mediaTrash";
import type { RetentionDays, TrashNoun } from "../../utils/mediaTrash";

const movieTrash = ref<TrashedMovie[]>([]);
const tvTrash = ref<TrashedTVShow[]>([]);
const animeTrash = ref<TrashedAnime[]>([]);
const loading = ref(false);
// the list stays on screen while it reloads after a restore or delete
const loadedOnce = ref(false);
const error = ref<string | null>(null);
// a title's id while it is restored/deleted, or "empty:<kind>" while emptying
const busyId = ref<string | null>(null);
const search = ref("");
const PAGE = 50;
const limits = ref<Record<Kind, number>>({
  movie: PAGE,
  tv: PAGE,
  anime: PAGE,
});

type Kind = "movie" | "tv" | "anime";
interface TrashRow {
  id: string;
  title: string;
  deleted_at: number;
}
// what differs between the three trashes; everything else is shared
const KINDS: Record<
  Kind,
  {
    label: string;
    // "the movie trash", "the TV show trash", "the anime trash"
    trash: string;
    noun: TrashNoun;
    // shows and anime take their seasons and episodes with them
    hasEpisodes: boolean;
    empty: (deletedThrough: number) => Promise<EmptyTrashResult>;
  }
> = {
  movie: {
    label: "Movies",
    trash: "movie",
    noun: { one: "movie", many: "movies" },
    hasEpisodes: false,
    empty: emptyMovieTrash,
  },
  tv: {
    label: "TV Shows",
    trash: "TV show",
    noun: { one: "TV show", many: "TV shows" },
    hasEpisodes: true,
    empty: emptyTVShowTrash,
  },
  anime: {
    label: "Anime",
    trash: "anime",
    noun: { one: "anime entry", many: "anime entries" },
    hasEpisodes: true,
    empty: emptyAnimeTrash,
  },
};
const groups = computed<
  {
    kind: Kind;
    label: string;
    total: number;
    matches: number;
    all: TrashRow[];
    rows: TrashRow[];
  }[]
>(() => {
  const q = search.value.trim().toLowerCase();
  const build = (kind: Kind, all: TrashRow[]) => {
    const found = q
      ? all.filter((r) => r.title.toLowerCase().includes(q))
      : all;
    return {
      kind,
      label: KINDS[kind].label,
      total: all.length,
      matches: found.length,
      all,
      rows: found.slice(0, limits.value[kind]),
    };
  };
  return [
    build("movie", movieTrash.value),
    build("tv", tvTrash.value),
    build("anime", animeTrash.value),
  ];
});

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    [movieTrash.value, tvTrash.value, animeTrash.value] = await Promise.all([
      fetchMovieTrash(),
      fetchTVShowTrash(),
      fetchAnimeTrash(),
    ]);
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Failed to load deleted media";
  } finally {
    loading.value = false;
    loadedOnce.value = true;
  }
}

// Opt-in retention: how long a trashed title stays before the server's
// regular clean-up deletes it for good. Never (0) is the default.
const retentionDays = ref<RetentionDays>(0);
// a failed load shows no choice at all rather than a guessed "Never"
const retentionState = ref<"loading" | "ready" | "unavailable">("loading");
const retentionError = ref<string | null>(null);
const retentionSaved = ref(false);
const retentionOptions = RETENTION_CHOICES.map((choice) => ({
  value: String(choice.value),
  label: choice.label,
}));

async function loadRetention() {
  try {
    retentionDays.value = (await fetchPreferences()).media_trash_retention_days;
    retentionState.value = "ready";
  } catch (e) {
    retentionState.value = "unavailable";
    retentionError.value =
      e instanceof Error
        ? `Could not load automatic deletion: ${e.message}`
        : "Could not load automatic deletion.";
  }
}

onMounted(() => Promise.all([loadAll(), loadRetention()]));

const nowSeconds = () => Math.floor(Date.now() / 1000);

async function chooseRetention(value: string) {
  const days = Number(value) as RetentionDays;
  const previous = retentionDays.value;
  if (days === previous) return;
  // turning deletion on, or shortening it, deletes sooner: confirm it first
  if (days && (!previous || days < previous)) {
    const all = [...movieTrash.value, ...tvTrash.value, ...animeTrash.value];
    // unknown while the trash has not loaded
    const due =
      loadedOnce.value && !error.value
        ? expiredUnder(all, days, nowSeconds())
        : null;
    const titles = { one: "title", many: "titles" };
    const ok = await confirm({
      title: "Delete trashed titles automatically?",
      message:
        `Movies, TV shows and anime that have been in the trash for ${days} days ` +
        "will be deleted permanently, with their seasons and episodes. " +
        (due === null
          ? "Titles already older than that will be deleted at the next clean-up. "
          : due
            ? `${countOf(due, titles)} already ${due === 1 ? "qualifies" : "qualify"} and will be deleted at the next clean-up. `
            : "Nothing in the trash qualifies yet. ") +
        "This can't be undone.",
      confirmLabel: `Delete after ${days} days`,
      danger: true,
    });
    if (!ok) return;
  }
  retentionDays.value = days;
  retentionError.value = null;
  try {
    const { prefs, latest } = await queuePreferences({
      media_trash_retention_days: days,
    });
    if (latest) {
      retentionDays.value = prefs.media_trash_retention_days;
      sharedPreferences.value = prefs;
    }
    retentionSaved.value = true;
    setTimeout(() => (retentionSaved.value = false), 1500);
  } catch (e) {
    retentionError.value =
      e instanceof Error
        ? `Automatic deletion was not changed: ${e.message}`
        : "Automatic deletion was not changed.";
    try {
      retentionDays.value = (
        await fetchPreferences()
      ).media_trash_retention_days;
    } catch {
      retentionDays.value = previous;
    }
  }
}

function formatDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function deadlineText(deletedAt: number): string {
  const deadline = retentionDeadline(deletedAt, retentionDays.value);
  if (deadline === null) return "";
  return deadline <= nowSeconds()
    ? "deleted for good at the next clean-up"
    : `kept until ${formatDate(deadline)}`;
}

async function restore(kind: Kind, id: string) {
  busyId.value = id;
  error.value = null;
  try {
    if (kind === "movie") await restoreMovie(id);
    else if (kind === "tv") await restoreTVShow(id);
    else await restoreAnime(id);
    await loadAll();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to restore";
  } finally {
    busyId.value = null;
  }
}

const confirm = useConfirm();
async function purge(kind: Kind, id: string, title: string) {
  const ok = await confirm({
    message: `Permanently delete "${title}"? This can't be undone.`,
    confirmLabel: "Delete forever",
    danger: true,
  });
  if (!ok) return;
  busyId.value = id;
  error.value = null;
  try {
    if (kind === "movie") await purgeMovie(id);
    else if (kind === "tv") await purgeTVShow(id);
    else await purgeAnime(id);
    await loadAll();
  } catch (e) {
    error.value =
      e instanceof Error ? e.message : "Failed to permanently delete";
  } finally {
    busyId.value = null;
  }
}

// what the last Empty trash of each kind did, until it is emptied again
const notices = ref<Partial<Record<Kind, { text: string; complete: boolean }>>>(
  {},
);
async function emptyKind(kind: Kind, rows: TrashRow[]) {
  const spec = KINDS[kind];
  const count = rows.length;
  if (!count || busyId.value) return;
  const ok = await confirm({
    title: `Empty the ${spec.trash} trash?`,
    message:
      `Permanently delete ${count === 1 ? "the" : "all"} ${countOf(count, spec.noun)} in the trash` +
      (spec.hasEpisodes
        ? `, with ${count === 1 ? "its" : "their"} seasons and episodes`
        : "") +
      "? This can't be undone." +
      (search.value.trim()
        ? " This includes titles hidden by the search."
        : ""),
    confirmLabel:
      count === 1 ? "Delete it forever" : `Delete all ${count} forever`,
    danger: true,
  });
  if (!ok) return;
  busyId.value = `empty:${kind}`;
  error.value = null;
  const cleared = { ...notices.value };
  delete cleared[kind];
  notices.value = cleared;
  // only what was on screen: a title trashed after confirming is kept
  const result = await spec.empty(newestDeletion(rows));
  notices.value = {
    ...notices.value,
    [kind]: {
      text: emptyTrashMessage(result, spec.noun, spec.trash),
      complete: !result.error && result.remaining === 0,
    },
  };
  busyId.value = null;
  await loadAll();
}
</script>

<template>
  <section class="settings-section">
    <h2>Media Trash</h2>
    <p class="section-hint">
      A deleted movie, show, or anime entry moves here first. Restore it to
      bring it back, or delete it for good: one title at a time, or a whole kind
      with Empty trash. Nothing is deleted automatically unless you choose a
      period below.
    </p>

    <div class="retention">
      <h3>Delete trashed titles automatically</h3>
      <div v-if="retentionError" class="form-error" role="alert">
        {{ retentionError }}
      </div>
      <template v-if="retentionState === 'ready'">
        <div class="retention-choice">
          <SegmentedControl
            :model-value="String(retentionDays)"
            :options="retentionOptions"
            @update:model-value="chooseRetention"
          />
          <span v-if="retentionSaved" class="saved">Saved</span>
        </div>
        <p class="section-hint retention-hint">
          <template v-if="retentionDays">
            Movies, TV shows and anime are deleted permanently, with their
            seasons and episodes, {{ retentionDays }} days after they were moved
            to the trash. The server's clean-up runs every six hours, so a title
            can stay a little past its date.
            <strong>This can't be undone.</strong>
          </template>
          <template v-else>
            Never: titles stay in the trash until you restore them or delete
            them yourself.
          </template>
        </p>
      </template>
    </div>

    <p v-if="!loadedOnce">Loading…</p>
    <div v-if="error" class="form-error">{{ error }}</div>

    <template v-if="loadedOnce">
      <input
        v-model="search"
        type="search"
        class="trash-search"
        placeholder="Search deleted titles"
        aria-label="Search deleted titles"
      />
      <div v-for="group in groups" :key="group.kind" class="trash-group">
        <div class="trash-group-header">
          <h3>
            {{ group.label }}
            <span v-if="group.total" class="trash-count">{{
              group.total
            }}</span>
          </h3>
          <button
            v-if="group.total"
            type="button"
            class="danger-button"
            :aria-label="`Empty trash: ${group.label}`"
            :disabled="busyId !== null"
            @click="emptyKind(group.kind, group.all)"
          >
            {{ busyId === `empty:${group.kind}` ? "Emptying…" : "Empty trash" }}
          </button>
        </div>
        <p
          v-if="notices[group.kind]"
          class="trash-notice"
          :class="{ incomplete: notices[group.kind]?.complete === false }"
          :role="notices[group.kind]?.complete ? 'status' : 'alert'"
        >
          {{ notices[group.kind]?.text }}
        </p>
        <p v-if="!group.total" class="empty-hint">Nothing in trash.</p>
        <p v-else-if="!group.matches" class="empty-hint">
          Nothing here matches the search.
        </p>
        <ul v-else class="trash-list">
          <li v-for="item in group.rows" :key="item.id" class="trash-row">
            <span class="trash-name">{{ item.title }}</span>
            <span class="trash-meta"
              >deleted {{ formatDate(item.deleted_at)
              }}<template v-if="retentionDays">
                · {{ deadlineText(item.deleted_at) }}</template
              ></span
            >
            <button
              type="button"
              class="secondary-button"
              :disabled="busyId === item.id || busyId === `empty:${group.kind}`"
              @click="restore(group.kind, item.id)"
            >
              Restore
            </button>
            <button
              type="button"
              class="danger-button"
              :disabled="busyId === item.id || busyId === `empty:${group.kind}`"
              @click="purge(group.kind, item.id, item.title)"
            >
              Delete forever
            </button>
          </li>
        </ul>
        <button
          v-if="group.matches > group.rows.length"
          type="button"
          class="secondary-button more-button"
          @click="limits[group.kind] += PAGE"
        >
          Show more ({{ group.matches - group.rows.length }} left)
        </button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 20px;
}
.trash-group {
  margin-bottom: 20px;
}
.trash-group h3 {
  margin: 0;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.trash-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 30px;
  margin-bottom: 8px;
}
.retention {
  margin-bottom: 22px;
}
.retention h3 {
  margin: 0 0 8px;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.retention-choice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.retention-hint {
  margin: 8px 0 0;
}
.saved {
  color: var(--ui-good);
  font-size: 0.82rem;
  font-weight: 700;
}
.trash-notice {
  margin: 0 0 8px;
  padding: 8px 10px;
  border-radius: var(--ui-radius-control);
  border: 1px solid color-mix(in srgb, var(--ui-good) 35%, transparent);
  background: color-mix(in srgb, var(--ui-good) 10%, transparent);
  color: var(--ui-text);
  font-size: 0.82rem;
  line-height: 1.5;
}
.trash-notice.incomplete {
  border-color: rgba(220, 38, 38, 0.3);
  background: rgba(220, 38, 38, 0.1);
}
.trash-search {
  width: 100%;
  max-width: 360px;
  box-sizing: border-box;
  margin-bottom: 18px;
  background: var(--ui-surface);
  color: #e5e5e5;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 8px 12px;
  font-size: 0.85rem;
}
.trash-count {
  margin-left: 6px;
  font-size: 0.72rem;
  font-weight: 600;
  color: #8a8a8a;
}
.more-button {
  margin-top: 10px;
}
.empty-hint {
  color: var(--ui-faint);
  font-size: 0.82rem;
}
.trash-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.trash-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: color-mix(in srgb, var(--ui-text) 3%, transparent);
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-control);
  font-size: 0.82rem;
}
.trash-name {
  flex: 1;
  color: var(--ui-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.trash-meta {
  color: var(--ui-faint);
  font-size: 0.76rem;
}
.secondary-button {
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
  color: var(--ui-text);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 6px 14px;
  font-weight: 600;
  font-size: 0.78rem;
  cursor: pointer;
}
.secondary-button:hover:not(:disabled) {
  background: color-mix(in srgb, var(--ui-text) 14%, transparent);
}
.secondary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.danger-button {
  background: rgba(220, 38, 38, 0.12);
  color: var(--ui-error);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 6px 14px;
  font-weight: 600;
  font-size: 0.78rem;
  cursor: pointer;
}
.danger-button:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.2);
}
.danger-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.form-error {
  color: var(--ui-error);
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-bottom: 16px;
}
/* phone width: title, then dates, then the actions, each on its own line */
@media (max-width: 560px) {
  .trash-row {
    flex-wrap: wrap;
    gap: 6px 12px;
  }
  .trash-name,
  .trash-meta {
    flex-basis: 100%;
  }
}
</style>
