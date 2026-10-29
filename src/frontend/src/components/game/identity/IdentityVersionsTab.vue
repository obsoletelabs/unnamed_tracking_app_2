<script setup lang="ts">
import { computed, ref } from "vue";
import {
  changeRelationship,
  createRelationship,
  endRelationship,
  mergeGames,
  separateGames,
  setRelationshipDisplay,
  unmergeGame,
  type RelationshipDisplay,
} from "../../../services/gameIdentity";
import { useConfirm } from "../../../state/dialog";
import {
  DISPLAY_CHOICES,
  RELATIONSHIP_KINDS,
  kindName,
  titleOf,
  type IdentityTabProps,
} from "../../../utils/identityTabs";
import IdentityGamePicker from "./IdentityGamePicker.vue";

const props = defineProps<IdentityTabProps>();
const confirm = useConfirm();

const memberIds = computed(() => props.data.versions.map((v) => v.game_id));
const mergeEdges = computed(() =>
  props.data.relationships.filter(
    (r) => r.kind === "same_game" && r.ended_at === null,
  ),
);
const related = computed(() =>
  props.data.relationships.filter(
    (r) => r.kind !== "same_game" && r.ended_at === null,
  ),
);
const ended = computed(() =>
  props.data.relationships.filter((r) => r.ended_at !== null),
);

function mergeEdgeOf(gameId: string) {
  return mergeEdges.value.find((r) => r.from_id === gameId);
}
function displayChoice(gameId: string): string {
  const edge = mergeEdgeOf(gameId);
  return edge?.display === "link" || edge?.display === "hidden"
    ? edge.display
    : "default";
}

const toMerge = ref("");
const mergeAsSameCopy = ref(false);
const toKeepApart = ref("");
const toRelate = ref("");
const relKind = ref("related_to");
const relLabel = ref("");
const relDisplay = ref<RelationshipDisplay>("default");

async function merge(): Promise<void> {
  const chosen = toMerge.value;
  if (!chosen) return;
  const mine = props.data.umbrella_id;
  const done = await props.act(async () => {
    try {
      await mergeGames(chosen, mine, false, "target", mergeAsSameCopy.value);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "";
      if (!/marked as separate/i.test(message)) throw cause;
      if (
        !(await confirm({
          title: "Reverse “keep separate”?",
          message:
            "You told us these games are different. Merging them reverses that decision. You can split them again later.",
          confirmLabel: "Merge anyway",
        }))
      )
        return;
      await mergeGames(chosen, mine, true, "target", mergeAsSameCopy.value);
    }
  });
  if (done) {
    toMerge.value = "";
    mergeAsSameCopy.value = false;
  }
}
async function split(gameId: string): Promise<void> {
  if (
    !(await confirm({
      title: "Split this version?",
      message: `“${titleOf(props.data, gameId)}” becomes its own library entry again. Its playtime, achievements and sources stay with it, and we will not suggest merging it back.`,
      confirmLabel: "Split",
    }))
  )
    return;
  await props.act(() => unmergeGame(gameId, true));
}
async function keepApart(): Promise<void> {
  if (!toKeepApart.value) return;
  const done = await props.act(() =>
    separateGames(props.data.umbrella_id, toKeepApart.value),
  );
  if (done) toKeepApart.value = "";
}
async function relate(): Promise<void> {
  if (!toRelate.value) return;
  const done = await props.act(() =>
    createRelationship({
      kind: relKind.value,
      fromId: toRelate.value,
      toId: props.data.umbrella_id,
      label: relKind.value === "custom" ? relLabel.value : "",
      display: relDisplay.value,
    }),
  );
  if (done) toRelate.value = "";
}
function setMergeDisplay(gameId: string, value: string): Promise<boolean> {
  const edge = mergeEdgeOf(gameId);
  if (!edge) return Promise.resolve(false);
  return props.act(() =>
    setRelationshipDisplay(edge.id, value as RelationshipDisplay),
  );
}
function kindChanged(
  id: string,
  kind: string,
  label: string,
): Promise<boolean> {
  return props.act(() => changeRelationship(id, { kind, label }));
}
</script>

<template>
  <div class="tab">
    <section aria-labelledby="idv-versions">
      <h3 id="idv-versions">Versions of this game</h3>
      <ul class="rows">
        <li v-for="v in data.versions" :key="v.game_id">
          <div class="grow">
            <RouterLink :to="`/games/${v.game_id}`">{{ v.title }}</RouterLink>
            <span v-if="v.game_id === data.umbrella_id" class="tag">Main</span>
            <p class="sub">
              {{ v.platform || "Unknown platform" }}
              <template v-if="v.playnite_guid"> · Playnite record</template>
            </p>
          </div>
          <template v-if="v.game_id !== data.umbrella_id">
            <label class="inline">
              <span>Shown in library</span>
              <select
                class="ui-field"
                :value="displayChoice(v.game_id)"
                :disabled="busy"
                @change="
                  setMergeDisplay(
                    v.game_id,
                    ($event.target as HTMLSelectElement).value,
                  )
                "
              >
                <option value="default">Under the main entry</option>
                <option value="link">As its own entry too</option>
                <option value="hidden">Folded away</option>
              </select>
            </label>
            <button
              type="button"
              class="ui-btn ui-btn-secondary"
              :disabled="busy"
              :aria-label="`Split ${v.title} from this game`"
              @click="split(v.game_id)"
            >
              Split
            </button>
          </template>
        </li>
      </ul>
      <form class="form" @submit.prevent="merge">
        <IdentityGamePicker
          v-model="toMerge"
          label="Merge another game into this one"
          :exclude="memberIds"
        />
        <label class="same-copy">
          <input v-model="mergeAsSameCopy" type="checkbox" />
          <span
            >Same copy, reported by another source (for example Playnite and the
            store's own sync). Count its playtime once. Different stores are
            different copies.</span
          >
        </label>
        <button
          class="ui-btn ui-btn-primary"
          type="submit"
          :disabled="busy || !toMerge"
        >
          Merge
        </button>
      </form>
      <form class="form" @submit.prevent="keepApart">
        <IdentityGamePicker
          v-model="toKeepApart"
          label="This is a different game"
          :exclude="memberIds"
        />
        <button
          class="ui-btn ui-btn-secondary"
          type="submit"
          :disabled="busy || !toKeepApart"
        >
          Keep separate
        </button>
      </form>
    </section>

    <section aria-labelledby="idv-relations">
      <h3 id="idv-relations">Relationships</h3>
      <p v-if="!related.length" class="sub">
        No other relationships. A DLC, remake or series link never merges
        playtime or achievements.
      </p>
      <ul class="rows">
        <li v-for="r in related" :key="r.id">
          <div class="grow">
            <strong>{{ titleOf(data, r.from_id) }}</strong>
            {{ kindName(r.kind, r.label).toLowerCase() }}
            <strong>{{ titleOf(data, r.to_id) }}</strong>
          </div>
          <label class="inline">
            <span>Type</span>
            <select
              class="ui-field"
              :value="r.kind"
              :disabled="busy || r.kind === 'custom'"
              @change="
                kindChanged(
                  r.id,
                  ($event.target as HTMLSelectElement).value,
                  '',
                )
              "
            >
              <option
                v-for="k in RELATIONSHIP_KINDS.filter(
                  (x) => x.value !== 'custom' || x.value === r.kind,
                )"
                :key="k.value"
                :value="k.value"
              >
                {{ k.label }}
              </option>
            </select>
          </label>
          <label class="inline">
            <span>In the library</span>
            <select
              class="ui-field"
              :value="r.display"
              :disabled="busy"
              @change="
                act(() =>
                  setRelationshipDisplay(
                    r.id,
                    ($event.target as HTMLSelectElement)
                      .value as RelationshipDisplay,
                  ),
                )
              "
            >
              <option
                v-for="c in DISPLAY_CHOICES"
                :key="c.value"
                :value="c.value"
              >
                {{ c.label }}
              </option>
            </select>
          </label>
          <button
            type="button"
            class="ui-btn ui-btn-ghost"
            :disabled="busy"
            :aria-label="`End relationship ${kindName(r.kind, r.label)} with ${titleOf(data, r.to_id)}`"
            @click="act(() => endRelationship(r.id))"
          >
            End
          </button>
        </li>
      </ul>
      <form class="form" @submit.prevent="relate">
        <IdentityGamePicker
          v-model="toRelate"
          label="Link another game to this one"
          :exclude="[data.umbrella_id]"
        />
        <label class="inline">
          <span>Is a…</span>
          <select v-model="relKind" class="ui-field">
            <option
              v-for="k in RELATIONSHIP_KINDS"
              :key="k.value"
              :value="k.value"
            >
              {{ k.label }}
            </option>
          </select>
        </label>
        <label v-if="relKind === 'custom'" class="inline">
          <span>Label</span>
          <input v-model="relLabel" class="ui-field" maxlength="100" required />
        </label>
        <label class="inline">
          <span>In the library</span>
          <select v-model="relDisplay" class="ui-field">
            <option
              v-for="c in DISPLAY_CHOICES"
              :key="c.value"
              :value="c.value"
            >
              {{ c.label }}
            </option>
          </select>
        </label>
        <button
          class="ui-btn ui-btn-secondary"
          type="submit"
          :disabled="busy || !toRelate"
        >
          Add relationship
        </button>
      </form>
      <details v-if="ended.length" class="history">
        <summary>{{ ended.length }} earlier relationships</summary>
        <ul class="rows">
          <li v-for="r in ended" :key="r.id" class="sub">
            {{ titleOf(data, r.from_id) }}
            {{ kindName(r.kind, r.label).toLowerCase() }}
            {{ titleOf(data, r.to_id) }}, ended
            {{ new Date((r.ended_at ?? 0) * 1000).toLocaleDateString() }}
          </li>
        </ul>
      </details>
    </section>
  </div>
</template>

<style scoped src="./identityTab.css"></style>
