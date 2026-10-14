<script setup lang="ts">
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
    selected.value = access.value.capabilities
      .filter((capability) => !capability.write)
      .map((capability) => capability.scope);
    hours.value = Math.min(24, access.value.maximum_grant_hours);
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
    <p class="eyebrow">Your account · Integration access</p>
    <h1 id="access-title">Choose what to share</h1>
    <p v-if="loading" role="status">Loading the access request…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="outcome">
      <h2>
        {{ outcome === "approved" ? "Access approved" : "Access denied" }}
      </h2>
      <p>You can return to the Activity. Your library and account stay here.</p>
      <RouterLink to="/settings?section=connected-apps"
        >Review connected apps</RouterLink
      >
    </template>
    <template v-else-if="access">
      <div class="identity">
        <h2>{{ access.name }}</h2>
        <p>{{ access.origin }}</p>
        <p v-if="access.external_account">
          External account:
          <strong>{{ access.external_account.username }}</strong> ·
          {{ access.external_account.subject }}<br />Reported by
          {{ access.name }}. Check this is the account you intend to link.
        </p>
        <p>
          Requests access to <strong>{{ instanceOrigin }}</strong> as
          <strong>{{ currentUser?.username }}</strong
          >.
        </p>
      </div>
      <template v-if="access.state === 'pending'">
        <p>
          Approve only the access you want. Write access is optional. These
          permissions never authorize administration or changes to another
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
            <select v-model.number="hours">
              <option value="until_revoked">Until revoked</option>
              <option v-for="value in choices" :key="value" :value="value">
                {{ value }} hours
              </option>
            </select>
          </label>
          <p v-if="hours === 'until_revoked'">
            Access continues until you revoke it or the instance owner disables
            the integration. Review ongoing access in Connected apps.
          </p>
          <p>
            You can reduce or revoke access in Connected apps. Revocation
            applies to the next request. Session sharing and private preferences
            are controlled separately in the Activity.
          </p>
          <div class="actions">
            <button type="submit" :disabled="saving || !selected.length">
              {{ saving ? "Saving…" : "Approve selected access" }}
            </button>
            <button
              type="button"
              class="secondary"
              :disabled="saving"
              @click="decide(false)"
            >
              Deny access
            </button>
          </div>
        </form>
      </template>
      <p v-else>
        This request has already been {{ access.state }}. Return to the Activity
        or review your connected apps.
      </p>
    </template>
  </section>
</template>

<style scoped>
.access-card {
  max-width: 720px;
  margin: 2rem auto;
  padding: 1.5rem;
  border: 1px solid var(--border-color, #444);
  border-radius: 16px;
}
.eyebrow {
  color: var(--text-muted);
  font-size: 0.85rem;
}
.identity {
  margin: 1.5rem 0;
  padding: 1rem;
  border: 1px solid var(--border-color, #444);
  border-radius: 10px;
  overflow-wrap: anywhere;
}
fieldset {
  display: grid;
  gap: 1rem;
  margin: 1.5rem 0;
  padding: 1rem;
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
button,
select {
  padding: 0.75rem 1rem;
  min-height: 44px;
}
.error {
  color: var(--danger, #c43737);
}
</style>
