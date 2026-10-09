<script setup lang="ts">
import PasswordInput from "../PasswordInput.vue";
import MetadataProviderSettings from "../plugins/MetadataProviderSettings.vue";
import { useMetadataSources } from "../../composables/useMetadataSources";
const {
  SHORT_DESC,
  VISUALS,
  expanded,
  toggleExpanded,
  psnStatus,
  psnLoading,
  npssoToken,
  psnConnecting,
  psnError,
  showDisconnectConfirm,
  handleConnectPsn,
  confirmDisconnectPsn,
  PROVIDER_CARDS,
  credentialStatus,
  fieldValues,
  cardSaving,
  cardError,
  cardInfo,
  credentialsLoading,
  passwordPlaceholder,
  statusLabel,
  statusClass,
  saveCard,
  clearCard,
  librarySyncing,
  handleSyncLibrary,
  formatLastSynced,
} = useMetadataSources();
</script>

<template>
  <section class="settings-section">
    <h2>Metadata/API</h2>
    <p class="section-hint">
      Search metadata and artwork through built-in and optional providers.
      Expand a tile to configure your account credentials or view its validation
      status.
    </p>
    <MetadataProviderSettings />

    <h3 class="group-heading">Achievements &amp; Accounts</h3>

    <div class="source-grid">
      <!-- GOG -->
      <div class="source-tile">
        <div
          class="tile-icon"
          :style="{ background: VISUALS.GOG.bg, color: VISUALS.GOG.fg }"
        >
          {{ VISUALS.GOG.mark }}
        </div>
        <div class="tile-body">
          <span class="tile-name" :title="PROVIDER_CARDS.GOG.description"
            >GOG</span
          >
          <span
            v-if="!credentialsLoading"
            class="tile-status"
            :class="statusClass('GOG')"
            >{{ statusLabel("GOG") }}</span
          >
        </div>
        <p class="tile-desc">GOG account connection</p>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded.GOG }"
            title="Configure"
            @click="toggleExpanded('GOG')"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
        </div>
        <form
          v-if="expanded.GOG"
          class="tile-form"
          @submit.prevent="saveCard(PROVIDER_CARDS.GOG)"
        >
          <label
            v-for="field in PROVIDER_CARDS.GOG.fields"
            :key="field.key"
            class="field"
          >
            <span>{{ field.label }}</span>
            <PasswordInput
              v-model="fieldValues.GOG[field.key]"
              :placeholder="passwordPlaceholder('GOG', field.key, field.label)"
            />
          </label>
          <div v-if="cardError.GOG" class="form-error">{{ cardError.GOG }}</div>
          <div class="card-actions">
            <button
              type="submit"
              class="primary-button"
              :disabled="cardSaving.GOG"
            >
              {{ cardSaving.GOG ? "Saving…" : "Save" }}
            </button>
            <button
              v-if="statusClass('GOG') !== 'disconnected'"
              type="button"
              class="secondary-button"
              @click="clearCard(PROVIDER_CARDS.GOG)"
            >
              Disconnect
            </button>
          </div>
        </form>
      </div>

      <!-- Steam -->
      <div class="source-tile">
        <div
          class="tile-icon"
          :style="{ background: VISUALS.Steam.bg, color: VISUALS.Steam.fg }"
        >
          {{ VISUALS.Steam.mark }}
        </div>
        <div class="tile-body">
          <span
            class="tile-name"
            title="Metadata search needs no account. Importing your library and achievements needs a Web API key + your profile."
            >Steam</span
          >
          <span
            v-if="credentialStatus.Steam?.display_name"
            class="tile-profile"
          >
            <img
              v-if="credentialStatus.Steam.avatar_url"
              :src="credentialStatus.Steam.avatar_url"
              class="tile-avatar"
              alt=""
            />
            {{ credentialStatus.Steam.display_name }}
          </span>
          <span
            v-else-if="!credentialsLoading"
            class="tile-status"
            :class="statusClass('Steam')"
            >{{ statusLabel("Steam") }}</span
          >
        </div>
        <p class="tile-desc">
          {{
            formatLastSynced(credentialStatus.Steam) ?? "Library & achievements"
          }}
        </p>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded.Steam }"
            title="Connect account to import your library"
            @click="toggleExpanded('Steam')"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
        </div>
        <button
          v-if="statusClass('Steam') !== 'disconnected'"
          type="button"
          class="import-button"
          :disabled="librarySyncing.steam"
          @click="handleSyncLibrary('steam')"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M23 4v6h-6M1 20v-6h6" />
            <path
              d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"
            />
          </svg>
          {{ librarySyncing.steam ? "Importing…" : "Import Library" }}
        </button>
        <form
          v-if="expanded.Steam"
          class="tile-form"
          @submit.prevent="saveCard(PROVIDER_CARDS.Steam)"
        >
          <p class="tile-hint">
            Both fields are required to import your library: your Steam profile
            link, vanity name or SteamID, and a Web API key. Your profile's game
            details must be set to Public.
          </p>
          <label
            v-for="field in PROVIDER_CARDS.Steam.fields"
            :key="field.key"
            class="field"
          >
            <span>{{ field.label }}</span>
            <PasswordInput
              v-if="field.type === 'password'"
              v-model="fieldValues.Steam[field.key]"
              :placeholder="
                passwordPlaceholder('Steam', field.key, field.label)
              "
            />
            <input
              v-else
              v-model="fieldValues.Steam[field.key]"
              type="text"
              autocomplete="off"
              :placeholder="`Paste your ${field.label.toLowerCase()}`"
            />
          </label>
          <div v-if="cardError.Steam" class="form-error">
            {{ cardError.Steam }}
          </div>
          <div v-if="cardInfo.Steam" class="form-success">
            {{ cardInfo.Steam }}
          </div>
          <div class="card-actions">
            <button
              type="submit"
              class="primary-button"
              :disabled="cardSaving.Steam"
            >
              {{ cardSaving.Steam ? "Connecting…" : "Connect" }}
            </button>
            <button
              v-if="statusClass('Steam') !== 'disconnected'"
              type="button"
              class="secondary-button"
              @click="clearCard(PROVIDER_CARDS.Steam)"
            >
              Disconnect
            </button>
          </div>
        </form>
      </div>

      <!-- RetroAchievements -->
      <div class="source-tile">
        <div
          class="tile-icon"
          :style="{
            background: VISUALS.RetroAchievements.bg,
            color: VISUALS.RetroAchievements.fg,
          }"
        >
          {{ VISUALS.RetroAchievements.mark }}
        </div>
        <div class="tile-body">
          <span
            class="tile-name"
            title="Connect your account to import retro games and unlocked achievements. Metadata credentials are configured separately above."
            >RetroAchievements</span
          >
          <span
            v-if="credentialStatus.RetroAchievements?.display_name"
            class="tile-profile"
          >
            <img
              v-if="credentialStatus.RetroAchievements.avatar_url"
              :src="credentialStatus.RetroAchievements.avatar_url"
              class="tile-avatar"
              alt=""
            />
            {{ credentialStatus.RetroAchievements.display_name }}
          </span>
          <span
            v-else-if="!credentialsLoading"
            class="tile-status"
            :class="statusClass('RetroAchievements')"
          >
            {{ statusLabel("RetroAchievements") }}
          </span>
        </div>
        <p class="tile-desc">Retro library &amp; achievements</p>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded.RetroAchievements }"
            title="Configure"
            @click="toggleExpanded('RetroAchievements')"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
          <a
            class="icon-btn"
            :href="PROVIDER_CARDS.RetroAchievements.linkUrl"
            target="_blank"
            rel="noopener noreferrer"
            title="Get a free key"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path
                d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"
              />
            </svg>
          </a>
        </div>
        <button
          v-if="statusClass('RetroAchievements') !== 'disconnected'"
          type="button"
          class="import-button"
          :disabled="librarySyncing.retroachievements"
          @click="handleSyncLibrary('retroachievements')"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M23 4v6h-6M1 20v-6h6" />
            <path
              d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"
            />
          </svg>
          {{
            librarySyncing.retroachievements ? "Importing…" : "Import Library"
          }}
        </button>
        <form
          v-if="expanded.RetroAchievements"
          class="tile-form"
          @submit.prevent="saveCard(PROVIDER_CARDS.RetroAchievements)"
        >
          <label
            v-for="field in PROVIDER_CARDS.RetroAchievements.fields"
            :key="field.key"
            class="field"
          >
            <span>{{ field.label }}</span>
            <PasswordInput
              v-if="field.type === 'password'"
              v-model="fieldValues.RetroAchievements[field.key]"
              :placeholder="
                passwordPlaceholder('RetroAchievements', field.key, field.label)
              "
            />
            <input
              v-else
              v-model="fieldValues.RetroAchievements[field.key]"
              type="text"
              autocomplete="off"
              :placeholder="`Paste your ${field.label.toLowerCase()}`"
            />
          </label>
          <div v-if="cardError.RetroAchievements" class="form-error">
            {{ cardError.RetroAchievements }}
          </div>
          <div class="card-actions">
            <button
              type="submit"
              class="primary-button"
              :disabled="cardSaving.RetroAchievements"
            >
              {{ cardSaving.RetroAchievements ? "Saving…" : "Save" }}
            </button>
            <button
              v-if="statusClass('RetroAchievements') !== 'disconnected'"
              type="button"
              class="secondary-button"
              @click="clearCard(PROVIDER_CARDS.RetroAchievements)"
            >
              Disconnect
            </button>
          </div>
        </form>
      </div>

      <!-- PlayStation -->
      <div class="source-tile">
        <div
          class="tile-icon"
          :style="{
            background: VISUALS.PlayStation.bg,
            color: VISUALS.PlayStation.fg,
          }"
        >
          {{ VISUALS.PlayStation.mark }}
        </div>
        <div class="tile-body">
          <span
            class="tile-name"
            title="Unofficial npsso token flow. Also powers PSN Store data."
            >PlayStation</span
          >
          <span
            v-if="psnStatus.connected && psnStatus.display_name"
            class="tile-profile"
          >
            <img
              v-if="psnStatus.avatar_url"
              :src="psnStatus.avatar_url"
              class="tile-avatar"
              alt=""
            />
            {{ psnStatus.display_name }}
          </span>
          <span
            v-else-if="!psnLoading"
            class="tile-status"
            :class="psnStatus.connected ? 'connected' : 'disconnected'"
          >
            {{ psnStatus.connected ? "Connected" : "Not connected" }}
          </span>
        </div>
        <p class="tile-desc">{{ SHORT_DESC.PlayStation }}</p>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded.PlayStation }"
            title="Configure"
            @click="toggleExpanded('PlayStation')"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
          <a
            class="icon-btn"
            href="https://www.playstation.com"
            target="_blank"
            rel="noopener noreferrer"
            title="playstation.com"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path
                d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"
              />
            </svg>
          </a>
        </div>
        <button
          v-if="psnStatus.connected"
          type="button"
          class="import-button"
          :disabled="librarySyncing.psn"
          @click="handleSyncLibrary('psn')"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M23 4v6h-6M1 20v-6h6" />
            <path
              d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"
            />
          </svg>
          {{ librarySyncing.psn ? "Importing…" : "Import Library" }}
        </button>
        <div v-if="expanded.PlayStation" class="tile-form">
          <template v-if="!psnLoading">
            <template v-if="!psnStatus.connected">
              <label class="field">
                <span>npsso token</span>
                <PasswordInput
                  v-model="npssoToken"
                  placeholder="Paste your npsso token"
                />
              </label>
              <div v-if="psnError" class="form-error">{{ psnError }}</div>
              <button
                type="button"
                class="primary-button"
                :disabled="psnConnecting"
                @click="handleConnectPsn"
              >
                {{ psnConnecting ? "Connecting…" : "Connect" }}
              </button>
            </template>
            <template v-else>
              <p class="tile-hint">
                Connected: reconnect with a fresh token to re-check it.
              </p>
              <div v-if="psnError" class="form-error">{{ psnError }}</div>
              <button
                type="button"
                class="secondary-button"
                @click="showDisconnectConfirm = true"
              >
                Disconnect
              </button>
            </template>
          </template>
        </div>
      </div>

      <!-- Xbox -->
      <div class="source-tile">
        <div
          class="tile-icon"
          :style="{ background: VISUALS.Xbox.bg, color: VISUALS.Xbox.fg }"
        >
          {{ VISUALS.Xbox.mark }}
        </div>
        <div class="tile-body">
          <span class="tile-name" :title="PROVIDER_CARDS.Xbox.description"
            >Xbox</span
          >
          <span
            v-if="!credentialsLoading"
            class="tile-status"
            :class="statusClass('Xbox')"
            >{{ statusLabel("Xbox") }}</span
          >
        </div>
        <p class="tile-desc">{{ SHORT_DESC.Xbox }}</p>
        <div class="tile-actions">
          <button
            type="button"
            class="icon-btn"
            :class="{ active: expanded.Xbox }"
            title="Configure"
            @click="toggleExpanded('Xbox')"
          >
            <svg
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"
              />
            </svg>
          </button>
        </div>
        <form
          v-if="expanded.Xbox"
          class="tile-form"
          @submit.prevent="saveCard(PROVIDER_CARDS.Xbox)"
        >
          <label
            v-for="field in PROVIDER_CARDS.Xbox.fields"
            :key="field.key"
            class="field"
          >
            <span>{{ field.label }}</span>
            <PasswordInput
              v-if="field.type === 'password'"
              v-model="fieldValues.Xbox[field.key]"
              :placeholder="passwordPlaceholder('Xbox', field.key, field.label)"
            />
            <input
              v-else
              v-model="fieldValues.Xbox[field.key]"
              type="text"
              autocomplete="off"
              :placeholder="`Paste your ${field.label.toLowerCase()}`"
            />
          </label>
          <div v-if="cardError.Xbox" class="form-error">
            {{ cardError.Xbox }}
          </div>
          <div class="card-actions">
            <button
              type="submit"
              class="primary-button"
              :disabled="cardSaving.Xbox"
            >
              {{ cardSaving.Xbox ? "Saving…" : "Save" }}
            </button>
            <button
              v-if="statusClass('Xbox') !== 'disconnected'"
              type="button"
              class="secondary-button"
              @click="clearCard(PROVIDER_CARDS.Xbox)"
            >
              Disconnect
            </button>
          </div>
        </form>
      </div>
    </div>

    <div
      v-if="showDisconnectConfirm"
      class="confirm-backdrop"
      @click.self="showDisconnectConfirm = false"
    >
      <div class="confirm-dialog">
        <h3>Disconnect PlayStation account?</h3>
        <p>You'll need to paste your npsso token again to reconnect.</p>
        <div class="confirm-actions">
          <button
            type="button"
            class="secondary-button"
            @click="showDisconnectConfirm = false"
          >
            Cancel
          </button>
          <button
            type="button"
            class="danger-button"
            @click="confirmDisconnectPsn"
          >
            Disconnect
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped src="../../styles/pages/metadata-sources.css" />
