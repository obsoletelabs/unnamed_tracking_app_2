<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import UiModal from "../UiModal.vue";
import SegmentedTabs from "../SegmentedTabs.vue";
import {
  fetchIdentitySources,
  type IdentitySources,
} from "../../services/gameIdentity";
import IdentityVersionsTab from "./identity/IdentityVersionsTab.vue";
import IdentitySourcesTab from "./identity/IdentitySourcesTab.vue";
import IdentityDecisionsTab from "./identity/IdentityDecisionsTab.vue";
import IdentityCountersTab from "./identity/IdentityCountersTab.vue";
import GameManualTrackingPanel from "./GameManualTrackingPanel.vue";

const props = defineProps<{ gameId: string; title: string }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const TABS = [
  { value: "versions", label: "Versions" },
  { value: "sources", label: "Sources" },
  { value: "decisions", label: "Decisions" },
  { value: "counters", label: "Counters" },
  { value: "manual", label: "Manual tracking" },
];
const tab = ref("versions");
const data = ref<IdentitySources | null>(null);
const loading = ref(true);
const loadError = ref<string | null>(null);
const busy = ref(false);
const actionError = ref<string | null>(null);
const changedOnce = ref(false);
const request = new AbortController();

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    data.value = await fetchIdentitySources(props.gameId, request.signal);
  } catch (cause) {
    if (request.signal.aborted) return;
    loadError.value =
      cause instanceof Error ? cause.message : "Could not load the identity.";
  } finally {
    loading.value = false;
  }
}

/** Runs one change, shows its error in place and refreshes what is shown. */
async function act(change: () => Promise<void>): Promise<boolean> {
  busy.value = true;
  actionError.value = null;
  try {
    await change();
    changedOnce.value = true;
    await load();
    return true;
  } catch (cause) {
    actionError.value =
      cause instanceof Error ? cause.message : "That change did not work.";
    return false;
  } finally {
    busy.value = false;
  }
}

function close(): void {
  if (changedOnce.value) emit("changed");
  emit("close");
}
const ready = computed(() => data.value !== null);
onMounted(load);
onBeforeUnmount(() => request.abort());
</script>

<template>
  <UiModal
    :title="`Advanced identity tools: ${title}`"
    description="Where each copy's values come from, past decisions and shared counters. Everyday changes are on the game page under Copies & versions."
    size="wide"
    @close="close"
  >
    <SegmentedTabs
      v-model="tab"
      :options="TABS"
      aria-label="Identity sections"
    />
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
    <p v-if="loading && !ready" class="hint" role="status">Loading…</p>
    <div v-else-if="loadError && !ready" class="error" role="alert">
      <span>{{ loadError }}</span>
      <button type="button" class="ui-btn ui-btn-secondary" @click="load">
        Retry
      </button>
    </div>
    <div v-else-if="data" class="panel" :aria-busy="busy">
      <IdentityVersionsTab
        v-if="tab === 'versions'"
        :data="data"
        :busy="busy"
        :act="act"
      />
      <IdentitySourcesTab
        v-else-if="tab === 'sources'"
        :data="data"
        :busy="busy"
        :act="act"
      />
      <IdentityDecisionsTab
        v-else-if="tab === 'decisions'"
        :data="data"
        :busy="busy"
        :act="act"
      />
      <IdentityCountersTab
        v-else-if="tab === 'counters'"
        :data="data"
        :busy="busy"
        :act="act"
      />
      <template v-else>
        <GameManualTrackingPanel
          :game-id="gameId"
          part="completion"
          @changed="changedOnce = true"
        />
        <GameManualTrackingPanel
          :game-id="gameId"
          part="achievements"
          @changed="changedOnce = true"
        />
      </template>
    </div>
    <template #footer>
      <button type="button" class="ui-btn ui-btn-primary" @click="close">
        Done
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.panel {
  margin-top: 16px;
  min-width: 0;
}
.hint {
  color: var(--ui-dim);
}
.error {
  color: var(--ui-danger);
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
</style>
