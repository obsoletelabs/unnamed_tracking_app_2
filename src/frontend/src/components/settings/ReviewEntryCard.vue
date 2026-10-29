<script setup lang="ts">
// One suggested pair from the review queue. It says what each record is and
// where it came from, then lets the person say what the two really are:
// the same game (grouped as copies), related but different games, or
// unrelated games that merely look alike.
import { computed, ref, useId } from "vue";
import GameOriginTags from "../game/GameOriginTags.vue";
import type {
  CopyDisplay,
  ReviewDifference,
  ReviewEntry,
  ReviewGame,
} from "../../services/identityReview";
import { RELATIONSHIP_KINDS } from "../../utils/identityTabs";
import { dayLabel, hoursLabel } from "../../utils/identityDisplay";
import { copyName, storeName } from "../../utils/gameCopies";
import {
  originSentence,
  reporterNames,
  sameCopyBlocker,
} from "../../utils/gameOrigin";
import type { ReviewVerdict } from "../../utils/reviewVerdict";

const props = defineProps<{ entry: ReviewEntry; busy: boolean }>();
const emit = defineEmits<{ resolve: [verdict: ReviewVerdict] }>();

const uid = useId();
type Choice = "same" | "related" | "separate";
const choice = ref<Choice>("same");
const mainId = ref(
  props.entry.second.playtime_seconds > props.entry.first.playtime_seconds
    ? props.entry.second.id
    : props.entry.first.id,
);
const display = ref<CopyDisplay>("default");
const copyBlocker = computed(() =>
  sameCopyBlocker(props.entry.first, props.entry.second),
);
// One copy seen by two sources is the usual cause of a "possible duplicate";
// another store or platform is a different copy of the game.
const sameCopy = ref(props.entry.kind === "duplicate" && !copyBlocker.value);
const relationKind = ref("remake_of");
const fromId = ref(props.entry.first.id);
const showDifferences = ref(false);

const sides = computed(() => [props.entry.first, props.entry.second]);
const from = computed(() =>
  fromId.value === props.entry.first.id
    ? props.entry.first
    : props.entry.second,
);
const other = computed(() =>
  from.value.id === props.entry.first.id
    ? props.entry.second
    : props.entry.first,
);
const kinds = RELATIONSHIP_KINDS.filter((kind) => kind.value !== "custom");
const kindLabel = computed(
  () => kinds.find((kind) => kind.value === relationKind.value)?.label ?? "",
);

const headline = computed(() =>
  props.entry.kind === "copy"
    ? `Another copy of ${props.entry.first.title}?`
    : `Possible duplicate: ${props.entry.first.title}`,
);

const story = computed(() => {
  const [first, second] = sides.value;
  const heard = (game: ReviewGame) => {
    const names = reporterNames(game).filter((n) => n !== "Added manually");
    return names.length ? names.join(" and ") : copyName(game);
  };
  const differs = (props.entry.evidence.conflicts ?? []).filter((part) =>
    ["platform", "store", "edition"].includes(part),
  );
  if (props.entry.kind === "copy" && differs.length)
    return `Same name, but a different ${differs.join(", ").replace(/, ([^,]*)$/, " or $1")}: ${copyName(first)} (from ${heard(first)}) and ${copyName(second)} (from ${heard(second)}).`;
  const matched = (props.entry.evidence.matched ?? [])
    .join(", ")
    .replaceAll("_", " ");
  return `Looks like the same ${storeName(first.source) ?? "manual"} entry reported twice, once by ${heard(first)} and once by ${heard(second)}${matched ? ` (matched on ${matched})` : ""}.`;
});

const submitLabel = computed(
  () =>
    ({
      same: "Group as one game",
      related: "Save relationship",
      separate: "Keep separate",
    })[choice.value],
);

const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  source: "Store",
  platform: "Platform",
  release_date: "Release",
  playtime_seconds: "Playtime",
  last_played_at: "Last played",
  external_id: "Import ID",
  provider_ids: "Provider IDs",
  status: "Status",
};
function show(row: ReviewDifference, value: unknown): string {
  if (value === null || value === undefined || value === "")
    return "Not recorded";
  if (row.field === "playtime_seconds") return hoursLabel(Number(value));
  if (row.field === "last_played_at") return dayLabel(Number(value));
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function statusLabel(game: ReviewGame): string {
  const words = game.status.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function submit(): void {
  if (choice.value === "same")
    emit("resolve", {
      action: "same",
      mainId: mainId.value,
      display: display.value,
      sameCopy: sameCopy.value && !copyBlocker.value,
    });
  else if (choice.value === "related")
    emit("resolve", {
      action: "related",
      kind: relationKind.value,
      fromId: fromId.value,
    });
  else emit("resolve", { action: "separate" });
}
</script>

<template>
  <article class="entry">
    <header>
      <strong>{{ headline }}</strong>
      <span class="confidence"
        >{{ Math.round(entry.confidence * 100) }}% likely</span
      >
    </header>
    <p class="story">{{ story }}</p>

    <div class="pair">
      <section
        v-for="game in sides"
        :key="game.id"
        class="side"
        :class="{ chosen: choice === 'same' && mainId === game.id }"
        :aria-label="game.title"
      >
        <RouterLink class="side-title" :to="`/games/${game.id}`">{{
          game.title
        }}</RouterLink>
        <GameOriginTags :game="game" />
        <dl class="facts">
          <dt>Played</dt>
          <dd>{{ hoursLabel(game.playtime_seconds) }}</dd>
          <dt>Last played</dt>
          <dd>{{ dayLabel(game.last_played_at) }}</dd>
          <dt>Status</dt>
          <dd>{{ statusLabel(game) }}</dd>
          <template v-if="game.release_date">
            <dt>Release</dt>
            <dd>{{ game.release_date.slice(0, 4) }}</dd>
          </template>
        </dl>
      </section>
    </div>

    <fieldset class="choices">
      <legend>What are these two?</legend>

      <label class="choice" :class="{ on: choice === 'same' }">
        <input v-model="choice" type="radio" :name="`${uid}-c`" value="same" />
        <span class="choice-body">
          <strong>The same game</strong>
          <span class="hint"
            >Group them as copies under one library entry. Each copy keeps its
            own playtime, achievements and saves, and the main copy's page shows
            them all.</span
          >
        </span>
      </label>
      <div v-if="choice === 'same'" class="options">
        <fieldset class="main-pick">
          <legend>Main copy</legend>
          <label v-for="game in sides" :key="game.id">
            <input
              v-model="mainId"
              type="radio"
              :name="`${uid}-main`"
              :value="game.id"
            />
            {{ originSentence(game) }}
          </label>
        </fieldset>
        <fieldset class="main-pick">
          <legend>Are they the same copy?</legend>
          <label>
            <input
              v-model="sameCopy"
              type="radio"
              :name="`${uid}-copy`"
              :value="true"
              :disabled="!!copyBlocker"
            />
            Same copy, seen by two sources. Count its playtime once.
          </label>
          <label>
            <input
              v-model="sameCopy"
              type="radio"
              :name="`${uid}-copy`"
              :value="false"
            />
            Different copies. Each keeps its own playtime.
          </label>
          <span v-if="copyBlocker" class="hint">{{ copyBlocker }}</span>
        </fieldset>
        <label class="field">
          <span>Show the other copy in the library</span>
          <select v-model="display" class="ui-field">
            <option value="default">Folded under the main entry</option>
            <option value="link">As its own entry too</option>
            <option value="hidden">Hidden</option>
          </select>
        </label>
      </div>

      <label class="choice" :class="{ on: choice === 'related' }">
        <input
          v-model="choice"
          type="radio"
          :name="`${uid}-c`"
          value="related"
        />
        <span class="choice-body">
          <strong>Related, but a different game</strong>
          <span class="hint"
            >A remake, port, DLC, sequel or similar. Both stay in your library
            and are linked, and this pair is not suggested again.</span
          >
        </span>
      </label>
      <div v-if="choice === 'related'" class="options">
        <div class="relation">
          <label class="field">
            <span>This one</span>
            <select v-model="fromId" class="ui-field">
              <option v-for="game in sides" :key="game.id" :value="game.id">
                {{ originSentence(game) }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>is a</span>
            <select v-model="relationKind" class="ui-field">
              <option
                v-for="kind in kinds"
                :key="kind.value"
                :value="kind.value"
              >
                {{ kind.label }}
              </option>
            </select>
          </label>
        </div>
        <p class="hint">
          “{{ from.title }}” ({{ originSentence(from) }}) → {{ kindLabel }} “{{
            other.title
          }}” ({{ originSentence(other) }})
        </p>
      </div>

      <label class="choice" :class="{ on: choice === 'separate' }">
        <input
          v-model="choice"
          type="radio"
          :name="`${uid}-c`"
          value="separate"
        />
        <span class="choice-body">
          <strong>Different games</strong>
          <span class="hint"
            >They only look alike. Keep both as they are and don't suggest this
            pair again.</span
          >
        </span>
      </label>
    </fieldset>

    <table v-if="showDifferences" class="differences">
      <caption class="sr-only">
        Differences
      </caption>
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">{{ entry.first.title }}</th>
          <th scope="col">{{ entry.second.title }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in entry.differences"
          :key="row.field"
          :class="{ differs: !row.same }"
        >
          <th scope="row">{{ FIELD_LABELS[row.field] || row.field }}</th>
          <td>{{ show(row, row.first) }}</td>
          <td>{{ show(row, row.second) }}</td>
        </tr>
      </tbody>
    </table>

    <div class="entry-actions">
      <button
        class="ui-btn ui-btn-ghost"
        type="button"
        @click="showDifferences = !showDifferences"
      >
        {{ showDifferences ? "Hide differences" : "Inspect differences" }}
      </button>
      <button
        class="ui-btn ui-btn-primary"
        type="button"
        :disabled="busy"
        @click="submit"
      >
        {{ submitLabel }}
      </button>
    </div>
  </article>
</template>

<style scoped>
.entry {
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 12px;
  padding: 14px 18px;
}
.confidence {
  background: var(--ui-info-soft);
  border-radius: 999px;
  color: var(--ui-info);
  font-size: 0.75rem;
  margin-left: 8px;
  padding: 2px 8px;
}
.story,
.hint {
  color: var(--ui-dim);
  line-height: 1.5;
  margin: 6px 0 12px;
}
.hint {
  display: block;
  font-size: 0.85rem;
  margin: 0;
}
.pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.side {
  display: grid;
  align-content: start;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  min-width: 0;
}
.side.chosen {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
}
.side-title {
  color: var(--ui-text);
  font-weight: 600;
  overflow-wrap: anywhere;
}
.facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 12px;
  margin: 0;
  font-size: 0.85rem;
}
.facts dt {
  color: var(--ui-dim);
}
.facts dd {
  margin: 0;
}
.choices {
  border: 0;
  display: grid;
  gap: 8px;
  margin: 0 0 12px;
  padding: 0;
}
.choices > legend {
  font-weight: 600;
  margin-bottom: 6px;
  padding: 0;
}
.choice {
  align-items: flex-start;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  gap: 10px;
  padding: 10px 12px;
}
.choice.on {
  border-color: var(--ui-accent-line);
}
.choice-body {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.options {
  display: grid;
  gap: 10px;
  margin: 0 0 4px 28px;
}
.main-pick {
  border: 0;
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  font-size: 0.9rem;
}
.main-pick legend {
  color: var(--ui-dim);
  font-size: 0.85rem;
  padding: 0;
}
.main-pick label {
  align-items: center;
  display: flex;
  gap: 6px;
}
.field {
  display: grid;
  gap: 4px;
  min-width: 0;
  font-size: 0.85rem;
}
.field span {
  color: var(--ui-dim);
}
.relation {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
.differences {
  border-collapse: collapse;
  font-size: 0.85rem;
  margin-bottom: 12px;
  width: 100%;
}
.differences th,
.differences td {
  border-bottom: 1px solid var(--ui-border-soft);
  overflow-wrap: anywhere;
  padding: 4px 8px;
  text-align: left;
}
.differs td {
  background: var(--ui-warning-soft);
}
.entry-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
