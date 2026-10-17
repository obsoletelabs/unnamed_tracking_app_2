<script setup lang="ts">
import { computed } from "vue";
import {
  mergeGames,
  type IdentityDecision,
} from "../../../services/gameIdentity";
import { useConfirm } from "../../../state/dialog";
import { outcomeLabel } from "../../../utils/identityDisplay";
import { titleOf, type IdentityTabProps } from "../../../utils/identityTabs";

const props = defineProps<IdentityTabProps>();
const confirm = useConfirm();

const current = computed(() =>
  props.data.decisions.filter((d) => d.superseded_at === null),
);
const rejections = computed(() =>
  current.value.filter((d) => d.outcome === "confirmed_separate"),
);
const history = computed(() =>
  props.data.decisions.filter((d) => d.superseded_at !== null),
);

function who(d: IdentityDecision): string {
  if (d.actor_kind === "user") return "You";
  if (d.actor_kind === "migration")
    return "Carried over from an earlier choice";
  return d.rule_version ? `Automatic rule ${d.rule_version}` : "Automatic";
}
function subject(d: IdentityDecision): string {
  if (d.observation_id) return "A source record";
  return `${titleOf(props.data, d.first_game_id)} and ${titleOf(props.data, d.second_game_id)}`;
}
function evidence(d: IdentityDecision) {
  const e = d.evidence ?? {};
  return [
    ["Matched", e.matched],
    ["Differed", e.conflicts],
    ["Missing", e.missing],
  ].filter(([, list]) => Array.isArray(list) && list.length) as [
    string,
    string[],
  ][];
}
async function mergeAnyway(d: IdentityDecision): Promise<void> {
  if (!d.first_game_id || !d.second_game_id) return;
  if (
    !(await confirm({
      title: "Merge these games?",
      message:
        "You kept these two separate before. Merging reverses that choice; you can split them again at any time.",
      confirmLabel: "Merge",
    }))
  )
    return;
  const [first, second] = [d.first_game_id, d.second_game_id];
  await props.act(() => mergeGames(first, second, true));
}
const when = (t: number) => new Date(t * 1000).toLocaleDateString();
</script>

<template>
  <div class="tab">
    <section aria-labelledby="idd-reject">
      <h3 id="idd-reject">Kept separate</h3>
      <p v-if="!rejections.length" class="sub">
        No pair is being kept apart. Pairs you reject are never suggested again.
      </p>
      <ul class="rows">
        <li v-for="d in rejections" :key="d.id">
          <div class="grow">
            {{ subject(d) }}
            <p class="sub">{{ who(d) }} · {{ when(d.created_at) }}</p>
          </div>
          <button
            v-if="d.first_game_id && d.second_game_id"
            type="button"
            class="ui-btn ui-btn-secondary"
            :disabled="busy"
            @click="mergeAnyway(d)"
          >
            Merge anyway
          </button>
        </li>
      </ul>
    </section>
    <section aria-labelledby="idd-why">
      <h3 id="idd-why">Why it is this way</h3>
      <p v-if="!current.length" class="sub">
        No decision has been recorded for this game. Records are grouped only
        when you ask or when the evidence is exact.
      </p>
      <ul class="rows">
        <li v-for="d in current" :key="d.id">
          <div class="grow">
            <strong>{{ outcomeLabel(d.outcome) }}</strong>
            <span v-if="d.confidence !== null" class="tag"
              >{{ Math.round(d.confidence * 100) }}% sure</span
            >
            <p>{{ subject(d) }}</p>
            <p class="sub">
              {{ who(d) }} · {{ when(d.created_at) }}
              <template v-if="d.reason"> · {{ d.reason }}</template>
            </p>
            <p v-for="[name, list] in evidence(d)" :key="name" class="sub">
              {{ name }}: {{ list.join(", ") }}
            </p>
          </div>
        </li>
      </ul>
      <details v-if="history.length" class="history">
        <summary>{{ history.length }} replaced decisions</summary>
        <ul class="rows">
          <li v-for="d in history" :key="d.id" class="sub">
            {{ outcomeLabel(d.outcome) }}: {{ subject(d) }} ({{ who(d) }},
            {{ when(d.created_at) }})
          </li>
        </ul>
      </details>
    </section>
  </div>
</template>

<style scoped src="./identityTab.css"></style>
