<script setup lang="ts">
// The consent page every user-approved integration opens: web integrations
// such as the Discord Activity, and first-party desktop apps such as the
// Playnite extension, which sign in here instead of using a pasted API key.
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { currentUser } from "../state/auth";
import {
  decideAccessRequest,
  fetchAccessRequest,
  type AccessRequestSummary,
} from "../services/delegation";

const route = useRoute();
const access = ref<AccessRequestSummary | null>(null);
const selected = ref<string[]>([]);
const hours = ref<number | "until_revoked">(24);
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const outcome = ref("");
const instanceOrigin = window.location.origin;
const desktop = computed(() => access.value?.kind === "desktop");
// Where the person goes back to after deciding.
const returnTo = computed(() =>
  desktop.value
    ? (access.value?.name.replace(/^Unnamed Tracking for /, "") ?? "the app")
    : "the Activity",
);
const choices = computed(() =>
  [...new Set([1, 24, 168, 336, access.value?.maximum_grant_hours ?? 1])]
    .filter((value) => value <= (access.value?.maximum_grant_hours ?? 1))
    .sort((a, b) => a - b),
);

async function decide(approve: boolean) {
  if (!access.value) return;
  saving.value = true;
  error.value = "";
  try {
    const result = await decideAccessRequest(
      access.value.id,
      approve,
      approve ? selected.value : [],
      hours.value === "until_revoked" ? null : hours.value,
    );
    outcome.value = result.state;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Unable to save your decision.";
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    access.value = await fetchAccessRequest(String(route.params.requestId));
    // A desktop app the person just signed in from asks only for what it
    // needs to sync, so its capabilities start selected and its access lasts
    // until revoked, like the API key it replaces. Web integrations keep
    // writes unchecked and a timed grant.
    selected.value = access.value.capabilities
      .filter((capability) => desktop.value || !capability.write)
      .map((capability) => capability.scope);
    hours.value = desktop.value
      ? "until_revoked"
      : Math.min(24, access.value.maximum_grant_hours);
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "This request is unavailable.";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section class="access-card" aria-labelledby="access-title">
    <p class="eyebrow">Your account · Connected apps</p>
    <h1 id="access-title">
      {{ desktop ? `Connect ${returnTo}` : "Choose what to share" }}
    </h1>
    <p v-if="loading" role="status">Loading the access request…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="outcome">
      <h2>
        {{ outcome === "approved" ? "Access approved" : "Access denied" }}
      </h2>
      <p>
        You can close this tab and return to {{ returnTo }}.
        <template v-if="desktop && outcome === 'approved'">
          It finishes signing in by itself within a few seconds.
        </template>
      </p>
      <RouterLink to="/settings?section=connected-apps"
        >Review connected apps</RouterLink
      >
    </template>
    <template v-else-if="access">
      <div class="identity">
        <h2>{{ access.name }}</h2>
        <p v-if="desktop" class="sub">
          Desktop app<template v-if="access.external_account">
            on <strong>{{ access.external_account.username }}</strong></template
          >. Only approve this if you just started signing in from it.
        </p>
        <template v-else>
          <p class="sub">{{ access.origin }}</p>
          <p v-if="access.external_account">
            External account:
            <strong>{{ access.external_account.username }}</strong> ·
            {{ access.external_account.subject }}<br />Reported by
            {{ access.name }}. Check this is the account you intend to link.
          </p>
        </template>
        <p>
          Requests access to <strong>{{ instanceOrigin }}</strong> as
          <strong>{{ currentUser?.username }}</strong
          ><template v-if="currentUser?.display_name">
            (shown as {{ currentUser.display_name }})</template
          >.
        </p>
      </div>
      <template v-if="access.state === 'pending'">
        <p>
          Approve only the access you want.
          <template v-if="!desktop">Write access is optional.</template>
          These permissions never authorize administration or changes to another
          person's account.
        </p>
        <form @submit.prevent="decide(true)">
          <fieldset>
            <legend>Requested capabilities</legend>
            <label
              v-for="capability in access.capabilities"
              :key="capability.scope"
              class="capability"
            >
              <input
                v-model="selected"
                type="checkbox"
                :value="capability.scope"
              />
              <span
                ><strong>{{ capability.write ? "Write" : "Read" }}</strong> ·
                {{ capability.description }}</span
              >
            </label>
          </fieldset>
          <label class="expiry"
            >Access duration
            <select v-model.number="hours" class="ui-field">
              <option value="until_revoked">Until revoked</option>
              <option v-for="value in choices" :key="value" :value="value">
                {{ value }} hours
              </option>
            </select>
          </label>
          <p v-if="hours === 'until_revoked'" class="sub">
            Access continues until you revoke it or the instance owner switches
            it off. Review ongoing access in Connected apps.
          </p>
          <p class="sub">
            You can reduce or revoke access in Connected apps at any time.
            Revocation applies to the app's next request.
          </p>
          <div class="actions">
            <button
              type="submit"
              class="ui-btn ui-btn-primary"
              :disabled="saving || !selected.length"
            >
              {{ saving ? "Saving…" : "Approve selected access" }}
            </button>
            <button
              type="button"
              class="ui-btn ui-btn-secondary"
              :disabled="saving"
              @click="decide(false)"
            >
              Deny access
            </button>
          </div>
        </form>
      </template>
      <p v-else>
        This request has already been {{ access.state }}. Return to
        {{ returnTo }} or review your connected apps.
      </p>
    </template>
  </section>
</template>

<style scoped>
.access-card {
  max-width: 720px;
  margin: 2rem auto;
  padding: 1.5rem;
  border: 1px solid var(--ui-border);
  border-radius: 16px;
  background: var(--ui-surface);
  color: var(--ui-text);
}
h1 {
  margin: 0.25rem 0 0;
}
.eyebrow,
.sub {
  color: var(--ui-dim);
  font-size: 0.9rem;
}
.identity {
  margin: 1.5rem 0;
  padding: 1rem;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  overflow-wrap: anywhere;
}
.identity h2 {
  margin: 0 0 0.25rem;
  font-size: 1.1rem;
}
fieldset {
  display: grid;
  gap: 1rem;
  margin: 1.5rem 0;
  padding: 1rem;
  border: 1px solid var(--ui-border);
  border-radius: 10px;
}
.capability {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
}
.capability input {
  margin-top: 0.25rem;
  flex-shrink: 0;
}
.expiry {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-top: 1.5rem;
}
.error {
  color: var(--ui-danger);
}
@media (max-width: 560px) {
  .access-card {
    margin: 1rem;
    padding: 1rem;
  }
  .actions .ui-btn {
    flex: 1 1 100%;
  }
}
</style>
