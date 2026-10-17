<script setup lang="ts">
// Create, copy and revoke the shareable live status links of one story. The
// full link exists only in the response that creates it; afterwards only its
// dates are shown, so it cannot be recovered from the server.
import { onMounted, ref } from "vue";
import {
  createLiveLink,
  listLiveLinks,
  revokeLiveLink,
} from "../services/notificationStories";
import type { LiveLink } from "../services/notificationStories";
import { formatStamp } from "../utils/richNodes";

const props = defineProps<{ storyId: string }>();
const links = ref<LiveLink[]>([]);
const days = ref(7);
const created = ref<string | null>(null);
const message = ref("");
const error = ref("");
const busy = ref(false);

async function load() {
  try {
    links.value = await listLiveLinks(props.storyId);
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Load failed";
  }
}
async function create() {
  busy.value = true;
  error.value = "";
  try {
    created.value =
      (await createLiveLink(props.storyId, days.value)).url ?? null;
    message.value = "Link created. Copy it now; it is not shown again.";
    await load();
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Create failed";
  } finally {
    busy.value = false;
  }
}
async function copy() {
  if (!created.value) return;
  try {
    await navigator.clipboard.writeText(created.value);
    message.value = "Link copied.";
  } catch {
    message.value = "Select the link and copy it manually.";
  }
}
async function revoke(link: LiveLink) {
  busy.value = true;
  try {
    await revokeLiveLink(props.storyId, link.id);
    message.value = "Link revoked. It no longer works.";
    await load();
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : "Revoke failed";
  } finally {
    busy.value = false;
  }
}
function state(link: LiveLink): string {
  if (link.revokedAt) return "Revoked";
  return link.expiresAt * 1000 <= Date.now() ? "Expired" : "Active";
}
onMounted(load);
</script>

<template>
  <section class="live-links ui-panel" aria-labelledby="live-links-heading">
    <h2 id="live-links-heading">Share the latest status</h2>
    <p class="hint">
      A live link shows only the current status and last update time, never the
      details above. Anyone who has it can open it until it expires or you
      revoke it.
    </p>
    <div class="create">
      <label
        >Expires after
        <select v-model.number="days">
          <option :value="1">1 day</option>
          <option :value="7">7 days</option>
          <option :value="30">30 days</option>
        </select></label
      >
      <button
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="busy"
        @click="create"
      >
        Create live link
      </button>
    </div>
    <div v-if="created" class="created">
      <label
        >New link
        <input
          type="text"
          readonly
          :value="created"
          @focus="
            $event.target && ($event.target as HTMLInputElement).select()
          "
      /></label>
      <button
        type="button"
        class="ui-btn ui-btn-sm ui-btn-secondary"
        @click="copy"
      >
        Copy link
      </button>
    </div>
    <p v-if="message" role="status" class="note">{{ message }}</p>
    <p v-if="error" role="alert" class="note error">{{ error }}</p>
    <ul v-if="links.length" class="list" aria-label="Live links">
      <li v-for="link in links" :key="link.id">
        <span>
          {{ state(link) }} · created {{ formatStamp(link.createdAt) }} ·
          expires {{ formatStamp(link.expiresAt) }}
        </span>
        <button
          v-if="state(link) === 'Active'"
          type="button"
          class="ui-btn ui-btn-sm ui-btn-danger-soft"
          :disabled="busy"
          :aria-label="`Revoke link created ${formatStamp(link.createdAt)}`"
          @click="revoke(link)"
        >
          Revoke
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.live-links {
  display: grid;
  gap: var(--ui-space-3);
  padding: var(--ui-space-4);
}
h2 {
  margin: 0;
  font-size: var(--ui-font-heading);
  font-weight: var(--ui-weight-heading);
}
.hint,
.note {
  margin: 0;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 1.5;
}
.error {
  color: var(--ui-error);
}
.create,
.created {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: var(--ui-space-3);
}
label {
  display: grid;
  gap: 4px;
  font-size: var(--ui-font-small);
  color: var(--ui-dim);
}
.created label {
  flex: 1;
  min-width: 220px;
}
select,
input {
  padding: 8px 10px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
  min-width: 0;
}
.list {
  display: grid;
  gap: var(--ui-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--ui-space-2);
  font-size: var(--ui-font-small);
  overflow-wrap: anywhere;
}
</style>
