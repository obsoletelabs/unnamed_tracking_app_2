<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import UiModal from "../UiModal.vue";
import PasswordInput from "../PasswordInput.vue";
import type { AdminUser } from "../../services/admin";
import {
  changeUserEmail,
  changeUserPassword,
  forcePasswordReset,
  listEmailDestinations,
  listUserAudit,
  requestEmailVerification,
  resendInvitation,
  revokeEmailVerification,
  sendResetLink,
  withdrawInvitation,
  type AuditEntry,
  type EmailDestination,
  type Invitation,
} from "../../services/adminUsers";

const props = defineProps<{
  user: AdminUser | null;
  invitation: Invitation | null;
  isSelf: boolean;
}>();
const emit = defineEmits<{ close: []; changed: [] }>();

type Pending = "email" | "password" | "force" | "withdraw" | null;
// Fixed at open: a resend replaces the invitation row but history stays under the original id.
const targetId = ref(props.user?.id ?? props.invitation?.id ?? "");
const targetName = computed(() => props.user?.username ?? "");
const locked = computed(() => props.isSelf || props.user?.is_primary === true);
const lockedReason = computed(() =>
  props.isSelf
    ? "Use your own profile settings to change your credentials."
    : "The primary user's credentials can only be changed by that account.",
);
const hasPassword = computed(
  () => props.user?.auth_source !== "sso" && props.user?.is_active !== false,
);

const newEmail = ref("");
const newPassword = ref("");
const pending = ref<Pending>(null);
const adminPassword = ref("");
const confirmName = ref("");
const busy = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const destinations = ref<EmailDestination[]>([]);
const audit = ref<AuditEntry[]>([]);

async function refresh() {
  try {
    audit.value = await listUserAudit(targetId.value);
    if (props.user)
      destinations.value = await listEmailDestinations(props.user.id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Could not load details";
  }
}
onMounted(refresh);

function ask(kind: Pending) {
  pending.value = kind;
  adminPassword.value = "";
  confirmName.value = "";
  error.value = null;
  notice.value = null;
}

const confirmReady = computed(() => {
  if (pending.value === "withdraw") return true;
  return (
    adminPassword.value.length > 0 && confirmName.value === targetName.value
  );
});

async function run(action: () => Promise<string>) {
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    notice.value = await action();
    pending.value = null;
    newPassword.value = "";
    adminPassword.value = "";
    emit("changed");
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "The action failed";
    await refresh();
  } finally {
    busy.value = false;
  }
}

const proof = () => ({
  adminPassword: adminPassword.value,
  confirmUsername: confirmName.value,
});

function confirmPending() {
  const id = targetId.value;
  if (pending.value === "email")
    return run(async () => {
      const done = await changeUserEmail(id, newEmail.value, proof());
      newEmail.value = "";
      return `Email changed to ${done.email}. Outstanding reset links were voided.`;
    });
  if (pending.value === "password")
    return run(async () => {
      const done = await changeUserPassword(id, newPassword.value, proof());
      return `Password replaced; ${done.sessions_revoked} session(s) signed out.`;
    });
  if (pending.value === "force")
    return run(async () => {
      const done = await forcePasswordReset(id, proof());
      return `Password voided and ${done.sessions_revoked} session(s) signed out. A reset link was queued for their verified recovery address.`;
    });
  if (pending.value === "withdraw")
    return run(async () => {
      await withdrawInvitation(id);
      return "Invitation withdrawn.";
    });
}

const resetLink = () =>
  run(async () => {
    await sendResetLink(targetId.value);
    return "Reset link queued for the user's verified recovery address.";
  });
const verify = (destinationId?: string) =>
  run(async () => {
    await requestEmailVerification(targetId.value, destinationId);
    return "Verification email queued. The mailbox owner must confirm it.";
  });
const unverify = (destinationId: string) =>
  run(async () => {
    await revokeEmailVerification(
      targetId.value,
      destinationId,
      targetName.value,
    );
    return "Verification withdrawn; it must be completed again.";
  });
const resend = () =>
  run(async () => {
    await resendInvitation(targetId.value);
    return "A fresh invitation link was sent; the previous link no longer works.";
  });

function when(seconds: number) {
  return new Date(seconds * 1000).toLocaleString();
}
</script>

<template>
  <UiModal
    :title="user ? `Edit ${user.username}` : 'Invited account'"
    :description="
      user
        ? 'Active account. Sensitive changes need your administrator password.'
        : 'Not an account yet: it becomes active when the recipient accepts.'
    "
    @close="emit('close')"
  >
    <div class="edit-body">
      <p class="state-line">
        <span v-if="user" class="chip active">Active account</span>
        <span v-else class="chip invited"
          >Invited ({{ invitation?.status }})</span
        >
        <span v-if="user?.is_admin" class="chip">Admin</span>
        <span v-if="user?.is_primary" class="chip">Primary</span>
        <span v-if="user?.auth_source === 'sso'" class="chip"
          >Single sign-on</span
        >
        <span v-if="user?.is_active === false" class="chip">Disabled</span>
      </p>
      <div v-if="notice" class="notice" role="status">{{ notice }}</div>
      <div v-if="error" class="form-error" role="alert">{{ error }}</div>

      <template v-if="invitation">
        <section>
          <h3>Invitation</h3>
          <p class="meta">
            Sent to <strong>{{ invitation.email }}</strong
            >. Expires {{ when(invitation.expires_at) }}. Delivery:
            {{ invitation.delivery_status ?? "not queued" }}.
          </p>
          <div class="row">
            <button type="button" class="btn" :disabled="busy" @click="resend">
              Resend invitation
            </button>
            <button
              type="button"
              class="btn danger"
              :disabled="busy || invitation.status === 'revoked'"
              @click="ask('withdraw')"
            >
              Withdraw
            </button>
          </div>
          <p class="meta">
            Resending replaces the previous link with a new single-use one.
          </p>
        </section>
      </template>

      <template v-if="user">
        <p v-if="locked" class="meta">{{ lockedReason }}</p>
        <section>
          <h3>Email</h3>
          <p class="meta">Current: {{ user.email }}</p>
          <div class="row">
            <input
              v-model="newEmail"
              type="email"
              aria-label="New email address"
              placeholder="new@example.com"
              :disabled="locked"
            />
            <button
              type="button"
              class="btn"
              :disabled="locked || busy || !newEmail"
              @click="ask('email')"
            >
              Change email
            </button>
          </div>
        </section>

        <section>
          <h3>Password</h3>
          <p v-if="!hasPassword" class="meta">
            This account has no local password (single sign-on or disabled).
          </p>
          <template v-else>
            <div class="row">
              <PasswordInput
                v-model="newPassword"
                mode="new"
                autocomplete="new-password"
                input-aria-label="New password"
                :disabled="locked"
              />
              <button
                type="button"
                class="btn"
                :disabled="locked || busy || !newPassword"
                @click="ask('password')"
              >
                Set password
              </button>
            </div>
            <div class="row">
              <button
                type="button"
                class="btn"
                :disabled="busy"
                @click="resetLink"
              >
                Send reset link
              </button>
              <button
                type="button"
                class="btn danger"
                :disabled="locked || busy"
                @click="ask('force')"
              >
                Force password reset
              </button>
            </div>
            <p class="meta">
              Reset links are only ever delivered to email addresses the user
              has already verified for recovery. Force reset also voids the
              current password and signs the user out.
            </p>
          </template>
        </section>

        <section>
          <h3>Email verification</h3>
          <p class="meta">
            Verification is proved by the mailbox owner and cannot be forced.
          </p>
          <ul v-if="destinations.length" class="destinations">
            <li v-for="d in destinations" :key="d.id">
              <span>{{ d.address }}</span>
              <span class="chip" :class="{ active: d.verified }">{{
                d.verified ? "Verified" : "Unverified"
              }}</span>
              <span v-if="d.recovery_allowed" class="chip">Recovery</span>
              <button
                v-if="!d.verified"
                type="button"
                class="btn small"
                :disabled="busy"
                @click="verify(d.id)"
              >
                Send verification
              </button>
              <button
                v-else
                type="button"
                class="btn small danger"
                :disabled="busy"
                @click="unverify(d.id)"
              >
                Withdraw
              </button>
            </li>
          </ul>
          <button
            v-if="!destinations.some((d) => d.is_account_email)"
            type="button"
            class="btn"
            :disabled="busy"
            @click="verify()"
          >
            Verify account email
          </button>
        </section>
      </template>

      <section v-if="pending" class="confirm" aria-live="polite">
        <h3>Confirm</h3>
        <template v-if="pending === 'withdraw'">
          <p class="meta">Withdraw this invitation? Its link stops working.</p>
        </template>
        <template v-else>
          <p class="meta">
            This changes sign-in credentials. Enter your administrator password
            and type <strong>{{ targetName }}</strong> to continue.
          </p>
          <label class="field"
            ><span>Your administrator password</span
            ><PasswordInput
              v-model="adminPassword"
              mode="replace"
              autocomplete="current-password"
              input-aria-label="Your administrator password"
          /></label>
          <label class="field"
            ><span>Type the username to confirm</span
            ><input v-model="confirmName" type="text" autocomplete="off"
          /></label>
        </template>
        <div class="row">
          <button type="button" class="btn" @click="pending = null">
            Cancel
          </button>
          <button
            type="button"
            class="btn danger"
            :disabled="busy || !confirmReady"
            @click="confirmPending"
          >
            {{ busy ? "Working…" : "Confirm" }}
          </button>
        </div>
      </section>

      <section>
        <h3>Activity</h3>
        <p v-if="!audit.length" class="meta">No administrator actions yet.</p>
        <ul v-else class="audit">
          <li v-for="entry in audit" :key="entry.id">
            <strong>{{ entry.action }}</strong>
            <span class="chip" :class="entry.outcome">{{ entry.outcome }}</span>
            <span class="meta"
              >{{ entry.actor }}, {{ when(entry.occurred_at) }}</span
            >
            <span v-if="entry.detail" class="meta">{{ entry.detail }}</span>
          </li>
        </ul>
      </section>
    </div>
    <template #footer>
      <button type="button" class="btn" @click="emit('close')">Close</button>
    </template>
  </UiModal>
</template>

<style scoped>
.edit-body {
  display: grid;
  gap: 18px;
}
section {
  display: grid;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--ui-border-soft);
}
h3 {
  margin: 0;
  font-size: 0.95rem;
  color: var(--ui-text);
}
.meta {
  margin: 0;
  color: var(--ui-dim);
  font-size: 0.82rem;
  overflow-wrap: anywhere;
}
.state-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
}
.row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.row > input,
.row > :deep(.password-input) {
  flex: 1 1 200px;
  min-width: 0;
}
input {
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 9px 12px;
  font: inherit;
}
.field {
  display: grid;
  gap: 6px;
  font-size: 0.85rem;
}
.btn {
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
  color: var(--ui-text);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 8px 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn.small {
  padding: 5px 10px;
  font-size: 12px;
}
.btn.danger {
  color: var(--ui-error);
}
.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.chip {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 999px;
  color: var(--ui-dim);
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
}
.chip.active,
.chip.success {
  color: var(--ui-accent-text);
  background: color-mix(in srgb, var(--ui-accent) 16%, transparent);
}
.chip.invited {
  color: var(--ui-warning);
  background: var(--ui-warning-soft);
}
.chip.denied,
.chip.refused {
  color: var(--ui-error);
  background: rgba(220, 38, 38, 0.1);
}
.destinations,
.audit {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}
.destinations li,
.audit li {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  align-items: center;
  font-size: 0.85rem;
  color: var(--ui-text);
}
.notice {
  color: var(--ui-text);
  background: color-mix(in srgb, var(--ui-accent) 12%, transparent);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  font-size: 0.85rem;
}
.form-error {
  color: var(--ui-error);
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  font-size: 13px;
}
.confirm {
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-row);
  padding: 14px;
}
</style>
