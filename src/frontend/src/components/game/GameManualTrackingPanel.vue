<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  createManualAchievement,
  fetchAchievementTotals,
  fetchCompletion,
  releaseCompletion,
  removeManualAchievement,
  setCompletion,
  setManualAchievementEarned,
  updateManualAchievement,
  type AchievementTotals,
  type CompletionTotals,
  type ManualAchievement,
} from "../../services/gameTracking";
import { useConfirm } from "../../state/dialog";

const props = defineProps<{
  gameId: string;
  part: "completion" | "achievements";
}>();
const emit = defineEmits<{ changed: [] }>();
const confirm = useConfirm();

const STATUSES = [
  "BACKLOG",
  "PLAYING",
  "ON_HOLD",
  "PLAYED",
  "BEATEN",
  "MASTERED",
  "DROPPED",
  "WISHLIST",
];
const label = (status: string) =>
  status.charAt(0) + status.slice(1).toLowerCase().replace(/_/g, " ");

const completion = ref<CompletionTotals | null>(null);
const totals = ref<AchievementTotals | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);
const editingId = ref<string | null>(null);
const adding = ref(false);
const draft = ref({
  title: "",
  description: "",
  category: "",
  iconUrl: "",
  scope: "version" as "version" | "umbrella",
});

async function load(): Promise<void> {
  error.value = null;
  try {
    if (props.part === "completion") {
      completion.value = await fetchCompletion(props.gameId);
    } else {
      totals.value = await fetchAchievementTotals(props.gameId);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load.";
  }
}
watch(() => [props.gameId, props.part], load, { immediate: true });

async function act(work: () => Promise<void>, changes = false): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    await work();
    await load();
    if (changes) emit("changed");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not save.";
  } finally {
    busy.value = false;
  }
}

const pick = (gameId: string, event: Event) =>
  act(async () => {
    completion.value = await setCompletion(
      gameId,
      (event.target as HTMLSelectElement).value,
    );
  }, true);
const release = (gameId: string) =>
  act(async () => {
    completion.value = await releaseCompletion(gameId);
  }, true);

function startAdd(): void {
  draft.value = {
    title: "",
    description: "",
    category: "",
    iconUrl: "",
    scope: "version",
  };
  editingId.value = null;
  adding.value = true;
}
function startEdit(item: ManualAchievement): void {
  draft.value = {
    title: item.title,
    description: item.description ?? "",
    category: item.category ?? "",
    iconUrl: "",
    scope: item.scope,
  };
  adding.value = false;
  editingId.value = item.id;
}
function cancel(): void {
  adding.value = false;
  editingId.value = null;
}
const blank = (value: string) => value.trim() || null;
const saveDraft = () =>
  act(async () => {
    const d = draft.value;
    if (!d.title.trim()) throw new Error("Give the achievement a title.");
    if (editingId.value) {
      await updateManualAchievement(editingId.value, {
        title: d.title,
        description: blank(d.description),
        category: blank(d.category),
        ...(d.iconUrl.trim() ? { iconUrl: d.iconUrl.trim() } : {}),
      });
    } else {
      await createManualAchievement(props.gameId, {
        title: d.title,
        description: blank(d.description),
        category: blank(d.category),
        iconUrl: blank(d.iconUrl),
        scope: d.scope,
      });
    }
    cancel();
  });
const toggle = (item: ManualAchievement) =>
  act(async () => {
    await setManualAchievementEarned(item.id, !item.achieved);
  });
async function remove(item: ManualAchievement): Promise<void> {
  if (
    await confirm({
      title: "Remove achievement",
      message: `Remove "${item.title}" from your tracking? Imported achievements are not affected.`,
      confirmLabel: "Remove",
    })
  )
    await act(async () => removeManualAchievement(item.id));
}
const when = (seconds: number | null) =>
  seconds ? new Date(seconds * 1000).toLocaleDateString() : "";
const manualItems = computed(() => totals.value?.manual ?? []);
const several = computed(() => (totals.value?.versions.length ?? 0) > 1);
</script>

<template>
  <section
    v-if="part === 'completion' && completion"
    class="manual-tracking"
    aria-labelledby="completion-title"
  >
    <header>
      <h2 id="completion-title">Completion</h2>
      <span
        class="summary"
        :class="{ partial: completion.partial }"
        role="status"
        >{{ completion.summary }}</span
      >
    </header>
    <ul>
      <li v-for="v in completion.versions" :key="v.gameId">
        <div class="name">
          <RouterLink v-if="v.gameId !== gameId" :to="`/games/${v.gameId}`">{{
            v.title
          }}</RouterLink>
          <strong v-else>{{ v.title }}</strong>
          <span class="hint">{{ v.platform || "Unspecified platform" }}</span>
        </div>
        <label class="status">
          <span class="sr-only">Status of {{ v.title }}</span>
          <select
            class="ui-field"
            :value="v.status"
            :disabled="busy"
            @change="pick(v.gameId, $event)"
          >
            <option v-for="s in STATUSES" :key="s" :value="s">
              {{ label(s) }}
            </option>
          </select>
        </label>
        <span class="origin">
          {{ v.userSet ? "Set by you" : "From imports" }}
        </span>
        <button
          v-if="v.userSet"
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy"
          @click="release(v.gameId)"
        >
          Let imports update
        </button>
      </li>
    </ul>
    <p class="hint">
      A status you set stays as you chose it; Steam, Playnite and other imports
      do not change it.
    </p>
    <p v-if="error" class="manual-error" role="alert">{{ error }}</p>
  </section>

  <section
    v-else-if="part === 'achievements' && totals"
    class="manual-tracking"
    aria-labelledby="manual-ach-title"
  >
    <header>
      <h2 id="manual-ach-title">Your achievements</h2>
      <span class="summary" role="status">{{
        totals.umbrella.summary ?? "None yet"
      }}</span>
      <button
        class="ui-btn ui-btn-secondary"
        type="button"
        :disabled="busy"
        @click="startAdd"
      >
        Add achievement
      </button>
    </header>
    <ul v-if="several" class="versions" aria-label="Achievements per version">
      <li v-for="v in totals.versions" :key="v.gameId">
        <span class="name"
          >{{ v.title }}<span class="hint">{{ v.platform || "" }}</span></span
        >
        <span
          >{{ v.unlocked }} of {{ v.total }} earned ({{
            v.manual.total
          }}
          yours)</span
        >
      </li>
    </ul>
    <form v-if="adding || editingId" class="editor" @submit.prevent="saveDraft">
      <label
        >Title
        <input v-model="draft.title" class="ui-field" maxlength="500" required
      /></label>
      <label
        >Description
        <textarea v-model="draft.description" class="ui-field" rows="2" />
      </label>
      <label
        >Category
        <input v-model="draft.category" class="ui-field" maxlength="100"
      /></label>
      <label
        >Icon address (optional)
        <input
          v-model="draft.iconUrl"
          class="ui-field"
          type="url"
          placeholder="https://"
      /></label>
      <label v-if="adding"
        >Applies to
        <select v-model="draft.scope" class="ui-field">
          <option value="version">This version only</option>
          <option value="umbrella">Every version of this game</option>
        </select>
      </label>
      <div class="actions">
        <button class="ui-btn ui-btn-primary" type="submit" :disabled="busy">
          {{ editingId ? "Save changes" : "Add" }}
        </button>
        <button
          class="ui-btn ui-btn-secondary"
          type="button"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
      </div>
    </form>
    <ul v-if="manualItems.length" class="items">
      <li v-for="item in manualItems" :key="item.id">
        <label class="earned">
          <input
            type="checkbox"
            :checked="item.achieved"
            :disabled="busy"
            @change="toggle(item)"
          />
          <span class="sr-only">Earned: </span>
          <img
            v-if="item.iconUrl"
            :src="item.iconUrl"
            alt=""
            width="32"
            height="32"
          />
          <span class="ach-name"
            >{{ item.title }}
            <span v-if="item.category" class="hint"
              >· {{ item.category }}</span
            ></span
          >
        </label>
        <span class="hint">{{
          item.achieved
            ? `Earned${item.achievedAt ? " " + when(item.achievedAt) : ""}`
            : "Not earned"
        }}</span>
        <span v-if="item.scope === 'umbrella'" class="hint">All versions</span>
        <p v-if="item.description" class="desc">{{ item.description }}</p>
        <div class="actions">
          <button
            class="ui-btn ui-btn-secondary"
            type="button"
            :disabled="busy"
            :aria-label="`Edit ${item.title}`"
            @click="startEdit(item)"
          >
            Edit
          </button>
          <button
            class="ui-btn ui-btn-secondary"
            type="button"
            :disabled="busy"
            :aria-label="`Remove ${item.title}`"
            @click="remove(item)"
          >
            Remove
          </button>
        </div>
      </li>
    </ul>
    <p v-else-if="!adding" class="hint">
      Track achievements the game does not report by itself. They are kept apart
      from Steam and other imports and are never replaced by a sync.
    </p>
    <p v-if="error" class="manual-error" role="alert">{{ error }}</p>
  </section>
  <p v-else-if="error" class="manual-error" role="alert">{{ error }}</p>
</template>

<style scoped>
.manual-tracking {
  padding: 20px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  margin-bottom: 24px;
}
header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
h2 {
  margin: 0;
  font-size: 1.15rem;
}
.summary {
  flex: 1;
  color: var(--ui-dim);
}
.summary.partial {
  color: var(--ui-accent-text);
}
ul {
  list-style: none;
  padding: 0;
  margin: 12px 0 0;
}
li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 12px 0;
  border-top: 1px solid var(--ui-border);
}
.name a {
  color: var(--ui-accent-text);
}
.name {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 140px;
  overflow-wrap: anywhere;
}
.hint {
  color: var(--ui-dim);
  font-size: 0.9rem;
}
.origin {
  font-size: 0.85rem;
  color: var(--ui-dim);
}
.earned {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 160px;
}
.earned img {
  border-radius: 6px;
}
.desc {
  flex-basis: 100%;
  margin: 0;
  overflow-wrap: anywhere;
}
.editor {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.editor label {
  display: grid;
  gap: 4px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.manual-error {
  color: var(--ui-danger);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
