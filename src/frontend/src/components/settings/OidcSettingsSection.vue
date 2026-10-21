<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  reactive,
  ref,
  toRaw,
  useId,
} from "vue";
import AppIcon from "../AppIcon.vue";
import PasswordInput from "../PasswordInput.vue";
import {
  fetchDeploymentSettings,
  updateDeploymentSettings,
  type DeploymentSettings,
  type OidcProviderSetting,
} from "../../services/deploymentSettings";

const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const saved = ref(false);
const dragIndex = ref<number | null>(null);
const browserOrigin = window.location.origin;
const login = reactive({ default_login_method: "local", enabled: true });
const providers = ref<OidcProviderSetting[]>([]);
const lockedFields = ref<Record<string, boolean>>({});
const root = ref<HTMLElement | null>(null);
const idPrefix = useId();
// Client-only row identity keeps keyed rendering, disclosure state and focus
// attached to a provider while it is reordered. It is never sent to the server.
const rowKeys = new WeakMap<OidcProviderSetting, string>();
let rowCount = 0;
const expandedRows = reactive(new Set<string>());
const orderStatus = ref("");
const anyExpanded = computed(() => providers.value.some(isExpanded));
function responseLocked(name: string) {
  return lockedFields.value[name] === true;
}

function editableProviders(response: DeploymentSettings) {
  return (response.oidc.named_providers ?? []).map((provider) => ({
    ...provider,
    client_secret: "",
    button_colour: provider.button_colour || "",
    autostart_enabled: provider.autostart_enabled !== false,
    require_verified_email: provider.require_verified_email === true,
  }));
}

function rowKey(provider: OidcProviderSetting) {
  const raw = toRaw(provider);
  let key = rowKeys.get(raw);
  if (!key) {
    key = `${idPrefix}-provider-${++rowCount}`;
    rowKeys.set(raw, key);
  }
  return key;
}

function providerLabel(provider: OidcProviderSetting, index: number) {
  return provider.name || `Provider ${index + 1}`;
}

function isExpanded(provider: OidcProviderSetting) {
  return expandedRows.has(rowKey(provider));
}

function toggleProvider(provider: OidcProviderSetting) {
  const key = rowKey(provider);
  if (!expandedRows.delete(key)) expandedRows.add(key);
}

function setAllExpanded(expanded: boolean) {
  expandedRows.clear();
  if (expanded)
    providers.value.forEach((provider) => expandedRows.add(rowKey(provider)));
}

function announcePosition(provider: OidcProviderSetting, index: number) {
  const name = provider.name || "Unnamed provider";
  orderStatus.value = `${name} moved to position ${index + 1} of ${providers.value.length}.`;
}

function newProvider(): OidcProviderSetting {
  return {
    name: "",
    slug: "",
    issuer_url: "",
    client_id: "",
    client_secret: "",
    scopes: "openid profile email",
    redirect_uri: null,
    groups_claim: "groups",
    admin_group: null,
    user_match_field: "email",
    allow_new_users: true,
    require_verified_email: false,
    button_text: "Continue with SSO",
    button_image_url: null,
    button_colour: "",
    enabled: true,
    show_on_login: true,
    autostart_enabled: true,
    client_secret_configured: false,
  };
}

function slugify(name: string) {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function addProvider() {
  const provider = newProvider();
  providers.value.push(provider);
  expandedRows.add(rowKey(provider));
}

function removeProvider(index: number) {
  const [removed] = providers.value.splice(index, 1);
  if (removed) expandedRows.delete(rowKey(removed));
}

function defaultProviderSlug(provider: OidcProviderSetting) {
  if (!provider.slug) provider.slug = slugify(provider.name);
}

function ensureSlugs() {
  const used = new Set<string>();
  providers.value.forEach((provider, index) => {
    const base =
      slugify(provider.slug || provider.name) || `provider-${index + 1}`;
    let slug = base;
    let suffix = 2;
    while (used.has(slug)) slug = `${base}-${suffix++}`;
    provider.slug = slug;
    used.add(slug);
  });
}

function redirectUri(provider: OidcProviderSetting) {
  const slug = provider.slug || slugify(provider.name) || "provider-slug";
  return `${browserOrigin}/api/auth/oidc/callback/${slug}`;
}

async function moveProvider(index: number, direction: -1 | 1) {
  const target = index + direction;
  if (target < 0 || target >= providers.value.length) return;
  const items = providers.value;
  [items[index], items[target]] = [items[target], items[index]];
  const moved = items[target];
  announcePosition(moved, target);
  // Keep keyboard focus with the provider that moved, so repeated presses keep
  // moving the same provider even though its row changes position.
  await nextTick();
  const key = rowKey(moved);
  const pressed = direction < 0 ? "up" : "down";
  const opposite = direction < 0 ? "down" : "up";
  const next = [pressed, opposite]
    .map((name) =>
      root.value?.querySelector<HTMLButtonElement>(`[id="${key}-${name}"]`),
    )
    .find((button) => button && !button.disabled);
  next?.focus();
}

function startDrag(index: number) {
  dragIndex.value = index;
}

function dropProvider(index: number) {
  if (dragIndex.value === null || dragIndex.value === index) {
    dragIndex.value = null;
    return;
  }
  const [item] = providers.value.splice(dragIndex.value, 1);
  providers.value.splice(index, 0, item);
  announcePosition(item, index);
  dragIndex.value = null;
}

function endDrag() {
  dragIndex.value = null;
}

function setProviderColour(provider: OidcProviderSetting, event: Event) {
  if (event.target instanceof HTMLInputElement)
    provider.button_colour = event.target.value;
}

onMounted(async () => {
  try {
    const response = await fetchDeploymentSettings();
    login.default_login_method =
      response.oidc.default_login_method === "sso" ? "sso" : "local";
    login.enabled = response.oidc.enabled !== false;
    lockedFields.value = response.oidc.locked_fields ?? {};
    // Saved providers start collapsed so the list can be scanned and reordered.
    providers.value = editableProviders(response);
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to load OIDC settings.";
  } finally {
    loading.value = false;
  }
});

async function save() {
  saving.value = true;
  error.value = null;
  saved.value = false;
  try {
    ensureSlugs();
    const providersToSave = providers.value.map((provider) => ({
      ...provider,
      // Always use the address currently being used to access Archive. The
      // displayed redirect URI is intentionally not user-editable.
      redirect_uri: redirectUri(provider),
    }));
    const response = await updateDeploymentSettings({
      oidc_enabled: login.enabled,
      oidc_default_login_method: login.default_login_method,
      oidc_providers_json: JSON.stringify(providersToSave),
    });
    // Saved slugs are unique, so they carry each row's disclosure state over to
    // the provider objects returned by the server.
    const expandedSlugs = new Set(
      providers.value.filter(isExpanded).map((provider) => provider.slug),
    );
    providers.value = editableProviders(response);
    expandedRows.clear();
    providers.value.forEach((provider) => {
      if (expandedSlugs.has(provider.slug)) expandedRows.add(rowKey(provider));
    });
    saved.value = true;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to save OIDC settings.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section ref="root" class="section">
    <h2>OpenID Connect / SSO</h2>
    <p class="hint">
      Configure one or more named identity providers. Providers can be
      reordered, styled, shown on the login page, or given independent autostart
      URLs.
    </p>

    <div v-if="loading">Loading…</div>
    <template v-else>
      <div class="login-panel">
        <div>
          <strong>Enable OIDC</strong>
          <p class="hint">
            When disabled, partially configured providers may be saved without
            being used for login.
          </p>
        </div>
        <input
          v-model="login.enabled"
          type="checkbox"
          :disabled="responseLocked('oidc_enabled')"
        />
      </div>

      <div class="login-panel">
        <div>
          <strong>Default login method</strong>
          <p class="hint">
            Choose whether the normal login page initially prioritises local
            credentials or SSO.
          </p>
        </div>
        <select v-model="login.default_login_method">
          <option value="local">Local username &amp; password</option>
          <option value="sso">SSO</option>
        </select>
      </div>

      <div class="providers-header">
        <div>
          <h3>Identity providers</h3>
          <p class="hint">
            Collapse providers to reorder them without scrolling through every
            setting. Drag using the provider header, or use the arrow controls.
            Order is reflected on the login page.
          </p>
        </div>
        <div class="providers-header-actions">
          <button
            v-if="providers.length > 1"
            type="button"
            class="secondary"
            @click="setAllExpanded(!anyExpanded)"
          >
            {{ anyExpanded ? "Collapse all" : "Expand all" }}
          </button>
          <button type="button" @click="addProvider">+ Add provider</button>
        </div>
      </div>

      <div v-if="!providers.length" class="empty">
        No named providers configured. Click <strong>+ Add provider</strong> to
        create one.
      </div>

      <div
        v-for="(provider, index) in providers"
        :key="rowKey(provider)"
        class="provider-card"
        :class="{ dragging: dragIndex === index }"
        @dragover.prevent
        @drop="dropProvider(index)"
      >
        <div
          class="provider-card-head"
          draggable="true"
          @dragstart="startDrag(index)"
          @dragend="endDrag"
        >
          <div class="provider-title">
            <span class="drag-handle" title="Drag to reorder" aria-hidden="true"
              >⠿</span
            >
            <h4>
              <button
                type="button"
                class="provider-toggle"
                :aria-expanded="isExpanded(provider)"
                :aria-controls="`${rowKey(provider)}-settings`"
                :title="
                  isExpanded(provider) ? 'Collapse settings' : 'Expand settings'
                "
                @click="toggleProvider(provider)"
              >
                <AppIcon
                  class="provider-chevron"
                  :class="{ expanded: isExpanded(provider) }"
                  name="chevron"
                  :size="16"
                />
                <span class="provider-identity">
                  <strong :id="`${rowKey(provider)}-name`">{{
                    providerLabel(provider, index)
                  }}</strong>
                  <small>{{
                    provider.slug
                      ? `/${provider.slug}`
                      : "Slug will be generated from the name"
                  }}</small>
                </span>
                <span
                  v-if="!provider.enabled || !provider.show_on_login"
                  class="provider-badges"
                >
                  <span v-if="!provider.enabled" class="provider-badge"
                    >Disabled</span
                  >
                  <span v-else class="provider-badge">Hidden from login</span>
                </span>
              </button>
            </h4>
          </div>
          <div class="provider-actions">
            <button
              :id="`${rowKey(provider)}-up`"
              type="button"
              class="move"
              :disabled="index === 0"
              title="Move up"
              :aria-label="`Move ${providerLabel(provider, index)} up`"
              @click.stop="moveProvider(index, -1)"
            >
              ↑
            </button>
            <button
              :id="`${rowKey(provider)}-down`"
              type="button"
              class="move"
              :disabled="index === providers.length - 1"
              title="Move down"
              :aria-label="`Move ${providerLabel(provider, index)} down`"
              @click.stop="moveProvider(index, 1)"
            >
              ↓
            </button>
            <button
              type="button"
              class="remove"
              :aria-label="`Remove ${providerLabel(provider, index)}`"
              @click.stop="removeProvider(index)"
            >
              Remove
            </button>
          </div>
        </div>

        <div
          v-show="isExpanded(provider)"
          :id="`${rowKey(provider)}-settings`"
          class="provider-body"
          role="group"
          :aria-labelledby="`${rowKey(provider)}-name`"
        >
          <div class="grid">
            <label
              ><span>Provider name</span
              ><input
                v-model="provider.name"
                placeholder="Authentik"
                @blur="defaultProviderSlug(provider)"
            /></label>
            <label
              ><span>Slug</span
              ><input
                v-model="provider.slug"
                placeholder="authentik"
                autocomplete="off"
            /></label>
            <label
              ><span>Issuer / discovery URL</span
              ><input
                v-model="provider.issuer_url"
                placeholder="https://id.example.com"
            /></label>
            <label
              ><span>Client ID</span><input v-model="provider.client_id"
            /></label>
            <label>
              <span>Client secret</span>
              <PasswordInput
                v-model="provider.client_secret"
                :mode="provider.client_secret_configured ? 'replace' : 'new'"
                :placeholder="
                  provider.client_secret_configured
                    ? 'Leave blank to keep saved secret'
                    : 'Required'
                "
                autocomplete="new-password"
                :required="
                  provider.enabled && !provider.client_secret_configured
                "
              />
            </label>
            <label
              ><span>Scopes</span><input v-model="provider.scopes"
            /></label>
            <label
              ><span>Groups claim</span
              ><input v-model="provider.groups_claim" placeholder="groups"
            /></label>
            <label
              ><span>Admin group</span
              ><input
                v-model="provider.admin_group"
                placeholder="archive-admins"
            /></label>
            <label
              ><span>User matching</span
              ><select v-model="provider.user_match_field">
                <option value="email">Email</option>
                <option value="username">Username</option>
                <option value="none">Disabled</option>
              </select></label
            >
            <label
              ><span>Login button text</span
              ><input
                v-model="provider.button_text"
                placeholder="Continue with Authentik"
            /></label>
            <div>
              <label
                ><input
                  type="checkbox"
                  :checked="!provider.button_colour"
                  @change="
                    provider.button_colour = provider.button_colour
                      ? ''
                      : '#d68a34'
                  "
                />
                Use app palette for this sign-in button</label
              >
              <label
                ><span>Provider button color</span>
                <div class="color-control">
                  <input
                    :value="provider.button_colour || '#d68a34'"
                    type="color"
                    :disabled="!provider.button_colour"
                    @input="setProviderColour(provider, $event)"
                  />
                  <input
                    v-model="provider.button_colour"
                    :disabled="!provider.button_colour"
                    class="color-text"
                    placeholder="#d68a34"
                    pattern="^#[0-9a-fA-F]{6}$"
                  />
                </div>
              </label>
            </div>
            <label
              ><span>Button image URL</span
              ><input
                v-model="provider.button_image_url"
                placeholder="Optional"
            /></label>
            <label class="full">
              <span
                >Redirect URI
                <small class="generated-label"
                  >Automatically generated</small
                ></span
              >
              <input
                :value="redirectUri(provider)"
                class="generated-input"
                type="text"
                readonly
                aria-readonly="true"
              />
              <small class="field-hint"
                >This is generated from the address currently used to access
                Archive and automatically overrides any previously saved
                redirect URI when you save.</small
              >
            </label>
          </div>

          <div class="provider-options">
            <label>
              <input
                v-model="provider.require_verified_email"
                type="checkbox"
              />
              Require verified email
            </label>
            <label
              ><input v-model="provider.allow_new_users" type="checkbox" />
              Allow new users</label
            >
            <label
              ><input v-model="provider.enabled" type="checkbox" /> Provider
              enabled</label
            >
            <label
              ><input v-model="provider.show_on_login" type="checkbox" /> Show
              on login page</label
            >
            <label
              ><input v-model="provider.autostart_enabled" type="checkbox" />
              Enable autostart URLs</label
            >
          </div>

          <div class="autostart">
            <small>
              Verified email is optional by default. When required, the provider
              must send email_verified=true; false, missing or malformed claims
              are rejected. Providers sharing an issuer and client ID use the
              strictest saved or environment requirement, including legacy
              callbacks.
            </small>
            <strong>Autostart URL</strong>
            <template v-if="provider.autostart_enabled">
              <span
                ><code
                  >{{ browserOrigin }}/login/{{
                    provider.slug || slugify(provider.name) || "provider-slug"
                  }}</code
                ></span
              >
              <small
                >This URL starts this provider directly. Disable autostart to
                make the direct link fall back to the normal login page.</small
              >
            </template>
            <small v-else
              >Autostart is disabled. The direct link for this provider will
              redirect to the normal login page.</small
            >
          </div>
        </div>
      </div>

      <p class="visually-hidden" role="status" aria-live="polite">
        {{ orderStatus }}
      </p>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="saved" class="success">OIDC settings saved.</p>
      <button :disabled="saving" @click="save">
        {{ saving ? "Saving…" : "Save OIDC settings" }}
      </button>
    </template>
  </section>
</template>

<style scoped>
.section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section h2 {
  margin: 0;
  color: var(--ui-text);
}

.hint {
  color: var(--ui-dim);
  font-size: 13px;
  line-height: 1.5;
}

.login-panel,
.provider-card {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 16px;
  background: var(--ui-surface);
}

.login-panel {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}

.login-panel > div {
  flex: 1 1 16rem;
  min-width: 0;
}

.login-panel select {
  flex-shrink: 0;
  width: min(100%, 18rem);
}

.login-panel select,
.grid input,
.grid select {
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  padding: 10px;
  font: inherit;
}

.providers-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.providers-header > div:first-child {
  flex: 1 1 16rem;
  min-width: 0;
}

.providers-header h3 {
  margin: 0;
  color: var(--ui-text);
}

.providers-header .hint {
  margin: 4px 0 0;
}

.providers-header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.providers-header button,
button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: 0;
  border-radius: var(--ui-radius-control);
  padding: 10px 14px;
  font-weight: 600;
  cursor: pointer;
}

.provider-card {
  cursor: default;
  transition: opacity var(--ui-motion-fast);
}

.provider-card.dragging {
  opacity: 0.55;
}

.provider-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  cursor: grab;
  user-select: none;
}

.provider-card-head:active {
  cursor: grabbing;
}

.provider-title {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  align-items: center;
  gap: 10px;
}

.provider-title h4 {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  font-size: inherit;
}

.provider-toggle {
  display: flex;
  width: 100%;
  min-height: var(--ui-control-height);
  align-items: center;
  gap: 10px;
  margin: -4px 0 -4px -8px;
  padding: 4px 8px;
  border-radius: var(--ui-radius-control);
  background: transparent;
  color: var(--ui-text);
  font: inherit;
  font-weight: inherit;
  text-align: left;
}

@media (hover: hover) {
  .provider-toggle:hover {
    background: var(--ui-surface-2);
  }
}

.provider-chevron {
  flex-shrink: 0;
  color: var(--ui-dim);
  transition: transform var(--ui-motion-fast);
}

.provider-chevron.expanded {
  transform: rotate(90deg);
}

.provider-identity {
  min-width: 0;
}

.provider-identity strong {
  display: block;
}

.provider-card-head small {
  display: block;
  color: var(--ui-faint);
  font-weight: 400;
  margin-top: 3px;
}

.provider-badges {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  margin-left: auto;
}

.provider-badge {
  padding: 2px 8px;
  border: 1px solid var(--ui-border-strong);
  border-radius: 999px;
  background: var(--ui-surface-2);
  color: var(--ui-dim);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.provider-body {
  margin-top: 14px;
}

.drag-handle {
  color: var(--ui-faint);
  font-size: 20px;
  cursor: grab;
}

.provider-actions {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
}

.provider-actions button {
  cursor: pointer;
}

.move,
.providers-header .secondary {
  background: var(--ui-surface-2);
  border: 1px solid var(--ui-border-strong);
  color: var(--ui-text);
}

.move {
  min-width: var(--ui-control-height);
  min-height: var(--ui-control-height);
  padding: 7px 10px;
}

.move:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.remove {
  min-height: var(--ui-control-height);
  background: transparent !important;
  border: 1px solid var(--ui-error) !important;
  color: var(--ui-error) !important;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.grid label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
  color: var(--ui-text);
  font-size: 13px;
}

.grid label.full {
  grid-column: 1/-1;
}

.color-control {
  display: grid;
  grid-template-columns: 48px 1fr;
  gap: 8px;
}

.color-control input[type="color"] {
  width: 48px;
  height: 40px;
  padding: 3px;
  background: var(--ui-bg);
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  cursor: pointer;
}

.color-text {
  min-width: 0;
}

.generated-input {
  background: var(--ui-surface-2) !important;
  color: var(--ui-faint) !important;
  border-color: var(--ui-border) !important;
  cursor: not-allowed;
}

.generated-label {
  color: var(--ui-faint);
  font-weight: 400;
  margin-left: 6px;
}

.field-hint {
  color: var(--ui-faint);
  font-size: 11px;
  line-height: 1.4;
}

.provider-options {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  margin-top: 14px;
  color: var(--ui-dim);
  font-size: 13px;
}

.provider-options label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.section input[type="checkbox"] {
  accent-color: var(--ui-accent-text);
}

.autostart {
  margin-top: 14px;
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-bg);
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--ui-text);
  font-size: 12px;
}

.autostart strong {
  color: var(--ui-text);
}

.autostart code {
  color: var(--ui-accent-text);
  overflow-wrap: anywhere;
}

.autostart small {
  color: var(--ui-faint);
}

.empty {
  border: 1px dashed var(--ui-border-strong);
  border-radius: var(--ui-radius-control);
  padding: 24px;
  color: var(--ui-faint);
  text-align: center;
}

.error {
  color: var(--ui-error);
}

.success {
  color: var(--ui-good);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

button:disabled {
  opacity: 0.6;
}

@media (max-width: 760px) {
  .grid {
    grid-template-columns: 1fr;
  }

  .grid label.full {
    grid-column: auto;
  }

  .providers-header,
  .login-panel {
    align-items: flex-start;
    flex-direction: column;
  }

  /* In a column, the desktop 16rem basis would become a 16rem height. */
  .providers-header > div:first-child,
  .login-panel > div {
    flex-basis: auto;
  }

  .provider-card-head {
    align-items: stretch;
    flex-direction: column;
  }

  .provider-actions {
    width: 100%;
  }

  .provider-actions .remove {
    margin-left: auto;
  }
}
</style>
