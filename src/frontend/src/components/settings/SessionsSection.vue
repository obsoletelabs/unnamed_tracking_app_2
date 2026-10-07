<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  fetchSessions,
  revokeSession,
  revokeSessions,
  sessionDevice,
  type BrowserSession,
  type SessionState,
} from "../../services/sessions";
import { checkAuth, currentUser, authCheckFailed } from "../../state/auth";
import { useConfirm } from "../../state/dialog";

const props = withDefaults(defineProps<{ admin?: boolean }>(), {
  admin: false,
});
const router = useRouter();
const confirm = useConfirm();
const sessions = ref<BrowserSession[]>([]);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const query = ref("");
const state = ref<SessionState | "">("");
const userId = ref("");
let generation = 0;

const users = computed(() =>
  Array.from(
    new Map(
      sessions.value.map((row) => [row.user_id, row.username ?? row.user_id]),
    ),
    ([id, name]) => ({ id, name }),
  ).sort((a, b) => a.name.localeCompare(b.name)),
);
const visibleSessions = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return sessions.value.filter(
    (row) =>
      (!state.value || row.state === state.value) &&
      (!props.admin || !userId.value || row.user_id === userId.value) &&
      (!needle ||
        [
          row.username,
          row.ip_address,
          row.user_agent,
          sessionDevice(row.user_agent),
        ].some((value) => value?.toLowerCase().includes(needle))),
  );
});
const canRevoke = computed(() =>
  sessions.value.some((row) => row.revoked_at === null),
);
const visibleUserCount = computed(
  () => new Set(visibleSessions.value.map((row) => row.user_id)).size,
);

function when(value: number | null) {
  return value == null
    ? "Unavailable"
    : new Date(value * 1000).toLocaleString();
}

async function load() {
  const request = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const rows = await fetchSessions(props.admin);
    if (request === generation) sessions.value = rows;
  } catch (err) {
    if (request === generation)
      error.value =
        err instanceof Error ? err.message : "Could not load sessions.";
  } finally {
    if (request === generation) loading.value = false;
  }
}

async function revoke(row?: BrowserSession, selectedUser = false) {
  if (busy.value || loading.value) return;
  const adminScope = props.admin;
  const targetUser = selectedUser ? userId.value : undefined;
  if (selectedUser && !targetUser) return;
  const message = row
    ? "Revoke this session? That browser will need to sign in again."
    : targetUser
      ? "Revoke every browser session for the selected user? This includes sessions hidden by your filters."
      : adminScope
        ? "Revoke every browser session for every user, including your current session? This includes sessions hidden by your filters."
        : "Revoke all your browser sessions, including this one? You will need to sign in again.";
  busy.value = true;
  if (
    !(await confirm({
      title: "Revoke sessions",
      message,
      confirmLabel: "Revoke",
      danger: true,
    }))
  ) {
    busy.value = false;
    return;
  }
  error.value = "";
  notice.value = "";
  try {
    if (row) {
      await revokeSession(row.id, adminScope);
      notice.value = "Session revoked.";
    } else {
      const result = await revokeSessions(adminScope, targetUser);
      notice.value = `${result.revoked} sessions revoked.`;
    }
    // Verify authentication before reloading: a bulk operation can revoke this browser.
    await checkAuth();
    if (authCheckFailed.value) {
      error.value =
        "Sessions were revoked, but authentication could not be checked. Refresh to continue.";
      return;
    }
    if (!currentUser.value) {
      await router.replace("/login");
      return;
    }
    await load();
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Could not revoke sessions.";
  } finally {
    busy.value = false;
  }
}

watch(
  () => props.admin,
  () => {
    sessions.value = [];
    query.value = "";
    state.value = "";
    userId.value = "";
    notice.value = "";
    void load();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  generation++;
});
</script>

<template>
  <section class="sessions-section">
    <div class="session-heading">
      <div>
        <h2>{{ admin ? "Session Manager" : "Sessions" }}</h2>
        <p>
          {{
            admin
              ? "Review browser sessions for everyone on this server."
              : "Review the browsers signed in to your account."
          }}
        </p>
      </div>
      <button type="button" :disabled="loading || busy" @click="load">
        Refresh
      </button>
    </div>
    <form class="session-filters" @submit.prevent>
      <div class="session-field">
        <label for="session-search">Search</label>
        <input
          id="session-search"
          v-model="query"
          type="search"
          maxlength="200"
          placeholder="IP address, browser or user"
        />
      </div>
      <div class="session-field">
        <label for="session-state">State</label>
        <select id="session-state" v-model="state">
          <option value="">All states</option>
          <option value="active">Active</option>
          <option value="expired">Expired</option>
          <option value="revoked">Revoked</option>
        </select>
      </div>
      <div v-if="admin" class="session-field">
        <label for="session-user">User</label>
        <select id="session-user" v-model="userId">
          <option value="">All users</option>
          <option v-for="user in users" :key="user.id" :value="user.id">
            {{ user.name }}
          </option>
        </select>
      </div>
    </form>
    <div class="session-actions">
      <button
        v-if="admin && userId"
        type="button"
        class="danger"
        :disabled="loading || busy || !canRevoke"
        @click="revoke(undefined, true)"
      >
        Revoke all for selected user
      </button>
      <button
        type="button"
        class="danger"
        :disabled="loading || busy || !canRevoke"
        @click="revoke()"
      >
        {{ admin ? "Revoke all server sessions" : "Revoke all my sessions" }}
      </button>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading" role="status" class="muted">Loading sessions…</p>
    <p v-else-if="!error && !visibleSessions.length" class="muted">
      No sessions match your filters.
    </p>
    <div
      v-else-if="admin"
      class="session-table"
      role="region"
      aria-label="Browser sessions"
      tabindex="0"
      :aria-busy="loading || busy"
    >
      <table>
        <caption>
          Sessions:
          {{
            visibleSessions.length
          }}
          · Users:
          {{
            visibleUserCount
          }}
        </caption>
        <thead>
          <tr>
            <th scope="col">User</th>
            <th scope="col">Browser</th>
            <th scope="col">IP address</th>
            <th scope="col">State</th>
            <th scope="col">Created</th>
            <th scope="col">Last active</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleSessions" :key="row.id">
            <th scope="row" class="session-user">
              {{ row.username ?? row.user_id }}
            </th>
            <td>
              {{ sessionDevice(row.user_agent) }}
              <details class="session-details">
                <summary>Details</summary>
                <p>Expires: {{ when(row.expires_at) }}</p>
                <p v-if="row.revoked_at !== null">
                  Revoked: {{ when(row.revoked_at) }}
                </p>
                <p v-if="row.user_agent" class="user-agent">
                  {{ row.user_agent }}
                </p>
              </details>
            </td>
            <td class="session-ip">{{ row.ip_address || "Unavailable" }}</td>
            <td>
              <span class="session-state" :data-state="row.state">{{
                row.state
              }}</span>
              <span v-if="row.is_current" class="current-session">Current</span>
            </td>
            <td class="session-time">{{ when(row.created_at) }}</td>
            <td class="session-time">{{ when(row.last_seen_at) }}</td>
            <td>
              <button
                v-if="row.state === 'active' && !row.is_current"
                type="button"
                class="danger"
                :aria-label="`Revoke session for ${row.username ?? row.user_id}`"
                :disabled="loading || busy"
                @click="revoke(row)"
              >
                Revoke
              </button>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="session-list" :aria-busy="loading || busy">
      <article
        v-for="row in visibleSessions"
        :key="row.id"
        class="session-card"
      >
        <div class="session-heading">
          <div>
            <h3>{{ sessionDevice(row.user_agent) }}</h3>
            <span class="session-state" :data-state="row.state">{{
              row.state
            }}</span>
            <span v-if="row.is_current" class="current-session"
              >Current session</span
            >
          </div>
          <button
            v-if="row.state === 'active' && !row.is_current"
            type="button"
            class="danger"
            :disabled="loading || busy"
            @click="revoke(row)"
          >
            Revoke
          </button>
        </div>
        <dl>
          <div>
            <dt>IP address</dt>
            <dd>{{ row.ip_address || "Unavailable" }}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{{ when(row.created_at) }}</dd>
          </div>
          <div>
            <dt>Last active</dt>
            <dd>{{ when(row.last_seen_at) }}</dd>
          </div>
          <div>
            <dt>Expires</dt>
            <dd>{{ when(row.expires_at) }}</dd>
          </div>
          <div v-if="row.revoked_at !== null">
            <dt>Revoked</dt>
            <dd>{{ when(row.revoked_at) }}</dd>
          </div>
        </dl>
        <details v-if="row.user_agent">
          <summary>Full user agent</summary>
          <p class="user-agent">{{ row.user_agent }}</p>
        </details>
      </article>
    </div>
    <p class="muted">
      Revoking sessions signs out browsers. API keys are managed separately.
    </p>
  </section>
</template>

<style scoped>
.sessions-section {
  color: var(--ui-text);
}
h2,
h3 {
  margin: 0 0 8px;
}
p {
  line-height: 1.5;
}
.session-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.session-heading > div {
  min-width: 0;
  overflow-wrap: anywhere;
}
.session-heading p {
  margin: 0 0 12px;
  color: var(--ui-dim);
}
.session-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 20px 0;
}
.session-field {
  display: grid;
  gap: 6px;
  flex: 1 1 160px;
  min-width: 0;
}
input,
select,
button {
  font: inherit;
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  min-height: var(--ui-control-height);
  padding: 8px 12px;
  box-sizing: border-box;
  max-width: 100%;
}
input,
select {
  width: 100%;
}
button,
summary {
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.session-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
}
.danger,
.error {
  color: var(--ui-error);
}
.muted,
dt {
  color: var(--ui-dim);
}
.session-list {
  display: grid;
  gap: 12px;
}
.session-table {
  overflow-x: auto;
  max-width: 100%;
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-card);
}
table {
  width: 100%;
  min-width: 800px;
  border-collapse: collapse;
  font-size: 0.875rem;
  text-align: left;
}
caption {
  padding: 10px 12px;
  text-align: left;
  color: var(--ui-dim);
}
th,
td {
  padding: 10px 12px;
  vertical-align: top;
  border-top: 1px solid var(--ui-border-soft);
}
thead th {
  background: var(--ui-surface);
  color: var(--ui-dim);
  white-space: nowrap;
}
.session-user,
.session-ip {
  white-space: nowrap;
}
.session-time {
  min-width: 115px;
}
.session-table .current-session {
  display: block;
  margin: 4px 0 0;
  white-space: nowrap;
}
.session-table button {
  min-height: 32px;
  padding: 4px 10px;
}
.session-details {
  margin-top: 5px;
  max-width: 240px;
}
.session-details p {
  margin: 8px 0;
}
.session-card {
  padding: 18px;
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-card);
  min-width: 0;
}
.session-state {
  text-transform: capitalize;
}
.session-state[data-state="active"] {
  color: var(--ui-good);
}
.current-session {
  margin-left: 12px;
  color: var(--ui-accent-text);
}
dl {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 200px), 1fr));
  gap: 12px;
  margin: 20px 0;
}
dt {
  font-size: 0.85em;
  margin-bottom: 4px;
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
summary {
  color: var(--ui-dim);
}
.user-agent {
  overflow-wrap: anywhere;
  font-size: 0.85em;
}
</style>
