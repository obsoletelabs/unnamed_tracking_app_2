<script setup lang="ts">
import { computed, ref } from "vue";
import { moveObservation } from "../../../services/gameIdentity";
import {
  agoLabel,
  hoursLabel,
  sourceName,
  dayLabel,
} from "../../../utils/identityDisplay";
import { titleOf, type IdentityTabProps } from "../../../utils/identityTabs";

const props = defineProps<IdentityTabProps>();
const moveTo = ref<Record<string, string>>({});

const byVersion = computed(() =>
  props.data.versions.map((version) => ({
    version,
    observations: props.data.observations.filter(
      (o) => o.game_id === version.game_id,
    ),
  })),
);

function facts(o: IdentityTabProps["data"]["observations"][number]): string[] {
  const out: string[] = [];
  const f = o.facts;
  if (typeof f.playtime_seconds === "number")
    out.push(`Playtime ${hoursLabel(f.playtime_seconds)}`);
  if (typeof f.last_played_at === "number")
    out.push(`Last played ${dayLabel(f.last_played_at)}`);
  if (typeof f.achievements_unlocked === "number")
    out.push(
      `Achievements ${f.achievements_unlocked}${typeof f.achievements_total === "number" ? ` / ${f.achievements_total}` : ""}`,
    );
  if (typeof f.status === "string") out.push(`Status ${f.status}`);
  if (typeof f.owned === "boolean") out.push(f.owned ? "Owned" : "Not owned");
  return out;
}
async function move(observationId: string): Promise<void> {
  const target = moveTo.value[observationId];
  if (!target) return;
  const done = await props.act(() => moveObservation(observationId, target));
  if (done) delete moveTo.value[observationId];
}
</script>

<template>
  <div class="tab">
    <p class="sub">
      Each source keeps its own record. Values here are what the source last
      reported; the overall numbers pick between them by the rules on the
      Counters tab. Nothing is deleted when you move or split a record.
    </p>
    <section
      v-for="{ version, observations } in byVersion"
      :key="version.game_id"
      :aria-label="`Sources for ${version.title}`"
    >
      <h3>
        {{ version.title }}
        <span class="tag">{{ version.platform || "Unknown platform" }}</span>
      </h3>
      <p v-if="version.playnite_guid" class="sub mono">
        Playnite GUID {{ version.playnite_guid }}
      </p>
      <p v-if="!observations.length" class="sub">
        No source has reported this record; its own saved values are used.
      </p>
      <ul class="rows">
        <li v-for="o in observations" :key="o.observation_id">
          <div class="grow">
            <strong>{{ sourceName(o.source) }}</strong>
            <span v-if="o.disconnected" class="tag">Disconnected</span>
            <span v-if="o.availability !== 'available'" class="tag">{{
              o.availability
            }}</span>
            <p class="sub">
              {{ o.title || "Untitled record" }}
              <template v-if="o.platform"> · {{ o.platform }}</template>
              <template v-if="o.edition"> · {{ o.edition }}</template>
            </p>
            <p class="sub mono">{{ o.source_kind }} · {{ o.external_id }}</p>
            <p class="sub">
              Updated {{ agoLabel(o.last_observed_at) }}
              <template v-if="facts(o).length">
                · {{ facts(o).join(" · ") }}
              </template>
            </p>
          </div>
          <label class="inline">
            <span>Move this record to</span>
            <select
              v-model="moveTo[o.observation_id]"
              class="ui-field"
              :disabled="busy || data.versions.length < 2"
            >
              <option value="">Choose a version…</option>
              <option
                v-for="other in data.versions.filter(
                  (v) => v.game_id !== o.game_id,
                )"
                :key="other.game_id"
                :value="other.game_id"
              >
                {{ titleOf(data, other.game_id) }}
              </option>
            </select>
          </label>
          <button
            type="button"
            class="ui-btn ui-btn-secondary"
            :disabled="busy || !moveTo[o.observation_id]"
            @click="move(o.observation_id)"
          >
            Move
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped src="./identityTab.css"></style>
