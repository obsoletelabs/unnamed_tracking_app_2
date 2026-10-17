<script setup lang="ts">
import { ref, onMounted, watch, computed } from "vue";
import { fetchScanSettings, updateScanSettings } from "../../services/settings";
import type { ScanSettingsUpdate } from "../../services/settings";
import { fetchMetadataProviders } from "../../services/metadata";
import type {
  MetadataMediaType,
  MetadataProviderStatus,
} from "../../services/metadata";
import { currentUser } from "../../state/auth";
import {
  MEDIA_TYPE_LABELS,
  METADATA_SETTINGS_LINKS,
  effectiveProviderOrder,
  moveProvider,
  participatesIn,
} from "../../utils/metadataProviders";
import type { ProviderOrderKind } from "../../utils/metadataProviders";
import ProviderOrderList from "./ProviderOrderList.vue";
import SegmentedControl from "./SegmentedControl.vue";
import ToggleButton from "./ToggleButton.vue";

const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const providerError = ref<string | null>(null);
const saveSuccess = ref(false);

// Core and plugin providers come from the same registry as Sources & API keys.
const providers = ref<MetadataProviderStatus[]>([]);
const providersLoaded = ref(false);
// Full saved orders (every provider, not only the visible media type).
const providerOrder = ref<string[]>([]);
const imageProviderOrder = ref<string[]>([]);
// An order is sent only after it was moved, so a toggle save never rewrites it.
const orderChanged = ref<Record<ProviderOrderKind, boolean>>({
  metadata: false,
  artwork: false,
});
const mediaType = ref<MetadataMediaType>("game");
const saveDeveloper = ref(true);
const savePublisher = ref(true);
const saveSeries = ref(true);
const saveTags = ref(true);
const saveFeatures = ref(true);
const saveDescription = ref(true);
const saveAgeRating = ref(true);
const saveReleaseDate = ref(true);
const saveTimeToBeat = ref(true);
const saveKeyArt = ref(true);
const saveBanner = ref(true);
const saveLogo = ref(true);
const saveIcon = ref(true);
const providerLastUsed = ref<Record<string, number>>({});

const isAdmin = computed(() => Boolean(currentUser.value?.is_admin));
const mediaTypeOptions = computed(() =>
  (Object.keys(MEDIA_TYPE_LABELS) as MetadataMediaType[])
    .filter((type) =>
      providers.value.some((provider) => provider.media_types.includes(type)),
    )
    .map((type) => ({ value: type, label: MEDIA_TYPE_LABELS[type] })),
);
const providersById = computed(
  () =>
    new Map(
      providers.value.map((provider) => [provider.provider_id, provider]),
    ),
);
const mediaTypesById = computed(
  () =>
    new Map(
      providers.value.map((provider) => [
        provider.provider_id,
        provider.media_types as readonly string[],
      ]),
    ),
);
// Only providers this account uses appear in its order; the server's own
// availability is shown on each row instead of hiding the provider.
function visible(kind: ProviderOrderKind): MetadataProviderStatus[] {
  const order =
    kind === "metadata" ? providerOrder.value : imageProviderOrder.value;
  return order
    .map((id) => providersById.value.get(id))
    .filter(
      (provider): provider is MetadataProviderStatus =>
        !!provider &&
        provider.account_enabled !== false &&
        provider.media_types.includes(mediaType.value) &&
        participatesIn(provider, kind),
    );
}
const metadataProviders = computed(() => visible("metadata"));
const artworkProviders = computed(() => visible("artwork"));
const optedOut = computed(() =>
  providers.value.filter(
    (provider) =>
      provider.account_enabled === false &&
      provider.media_types.includes(mediaType.value),
  ),
);

function move(kind: ProviderOrderKind, providerId: string, direction: -1 | 1) {
  const order = kind === "metadata" ? providerOrder : imageProviderOrder;
  const next = moveProvider(
    order.value,
    visible(kind).map((provider) => provider.provider_id),
    providerId,
    direction,
    mediaTypesById.value,
  );
  if (!next) return;
  order.value = next;
  orderChanged.value[kind] = true;
}

onMounted(async () => {
  const [settingsResult, providersResult] = await Promise.allSettled([
    fetchScanSettings(),
    fetchMetadataProviders(),
  ]);
  if (providersResult.status === "fulfilled") {
    providers.value = providersResult.value;
    providersLoaded.value = true;
    if (!mediaTypeOptions.value.some((item) => item.value === mediaType.value))
      mediaType.value = mediaTypeOptions.value[0]?.value ?? "game";
  } else {
    providerError.value =
      providersResult.reason instanceof Error
        ? providersResult.reason.message
        : "Provider status is unavailable.";
  }
  try {
    if (settingsResult.status === "rejected") throw settingsResult.reason;
    const settings = settingsResult.value;
    providerOrder.value = effectiveProviderOrder(
      providers.value,
      "metadata",
      settings.provider_order,
    );
    imageProviderOrder.value = effectiveProviderOrder(
      providers.value,
      "artwork",
      settings.image_provider_order,
    );
    saveDeveloper.value = settings.save_developer;
    savePublisher.value = settings.save_publisher;
    saveSeries.value = settings.save_series;
    saveTags.value = settings.save_tags;
    saveFeatures.value = settings.save_features;
    saveDescription.value = settings.save_description;
    saveAgeRating.value = settings.save_age_rating;
    saveReleaseDate.value = settings.save_release_date;
    saveTimeToBeat.value = settings.save_time_to_beat;
    saveKeyArt.value = settings.save_key_art;
    saveBanner.value = settings.save_banner;
    saveLogo.value = settings.save_logo;
    saveIcon.value = settings.save_icon;
    providerLastUsed.value = settings.provider_last_used;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to load scan settings";
  } finally {
    loading.value = false;
  }
});

// same as Profile/Appearance: without this, "Scan settings saved." keeps
// showing after a save even once the user starts changing something else,
// reading as if the new change was already saved
watch(
  [
    providerOrder,
    imageProviderOrder,
    saveDeveloper,
    savePublisher,
    saveSeries,
    saveTags,
    saveFeatures,
    saveDescription,
    saveAgeRating,
    saveReleaseDate,
    saveTimeToBeat,
    saveKeyArt,
    saveBanner,
    saveLogo,
    saveIcon,
  ],
  () => {
    saveSuccess.value = false;
  },
  { deep: true },
);

async function save() {
  saving.value = true;
  error.value = null;
  saveSuccess.value = false;
  const orders: ScanSettingsUpdate = {};
  if (providersLoaded.value && orderChanged.value.metadata)
    orders.provider_order = providerOrder.value;
  if (providersLoaded.value && orderChanged.value.artwork)
    orders.image_provider_order = imageProviderOrder.value;
  try {
    await updateScanSettings({
      ...orders,
      save_developer: saveDeveloper.value,
      save_publisher: savePublisher.value,
      save_series: saveSeries.value,
      save_tags: saveTags.value,
      save_features: saveFeatures.value,
      save_description: saveDescription.value,
      save_age_rating: saveAgeRating.value,
      save_release_date: saveReleaseDate.value,
      save_time_to_beat: saveTimeToBeat.value,
      save_key_art: saveKeyArt.value,
      save_banner: saveBanner.value,
      save_logo: saveLogo.value,
      save_icon: saveIcon.value,
    });
    orderChanged.value = { metadata: false, artwork: false };
    saveSuccess.value = true;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to save scan settings";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="settings-section">
    <h2>Scan Settings</h2>
    <p class="section-hint">
      Controls how future metadata searches behave: which provider is checked
      first, and which fields a search result is allowed to save onto a game.
    </p>

    <p v-if="loading">Loading…</p>
    <template v-else>
      <div class="field-block">
        <span class="field-label">Provider order</span>
        <p class="field-sublabel">
          Lists the built-in and plugin providers this server offers that your
          account uses. Providers the server cannot use yet keep their place and
          show why. A provider that covers several media types has one shared
          position.
        </p>
        <p v-if="providerError" class="form-error" role="alert">
          {{ providerError }} Provider order cannot be changed right now.
        </p>
        <SegmentedControl
          v-else-if="mediaTypeOptions.length > 1"
          :model-value="mediaType"
          :options="mediaTypeOptions"
          aria-label="Media type"
          @update:model-value="mediaType = $event as MetadataMediaType"
        />
      </div>
      <div v-if="providersLoaded" class="provider-order-row">
        <ProviderOrderList
          id="metadata-provider-order"
          title="Search & metadata order"
          hint="Whose details win when several providers describe the same title, and which equally good match is listed first."
          :providers="metadataProviders"
          :last-used="providerLastUsed"
          :is-admin="isAdmin"
          :disabled="saving"
          @move="(id, direction) => move('metadata', id, direction)"
        />
        <ProviderOrderList
          id="artwork-provider-order"
          title="Artwork order"
          hint="Whose artwork is offered first when several providers have art."
          :providers="artworkProviders"
          :last-used="providerLastUsed"
          :is-admin="isAdmin"
          :disabled="saving"
          @move="(id, direction) => move('artwork', id, direction)"
        />
      </div>
      <p v-if="optedOut.length" class="field-sublabel opted-out">
        Not used by your account:
        {{ optedOut.map((provider) => provider.name).join(", ") }}.
        <router-link :to="METADATA_SETTINGS_LINKS.sources"
          >Turn providers on in Sources &amp; API keys</router-link
        >
      </p>

      <div class="field-block">
        <span class="field-label">Images to save from a search result</span>
        <p class="field-sublabel">
          Turn an image type off to leave it untouched by search/refresh: useful
          if you keep your own art for something and don't want it overwritten.
        </p>
        <div class="refresh-options">
          <ToggleButton v-model="saveKeyArt" label="Key art">
            <strong>Key art</strong>: cover/box art, used as the game's poster
          </ToggleButton>
          <ToggleButton v-model="saveBanner" label="Banner">
            <strong>Banner</strong>: wide hero image for detail pages
          </ToggleButton>
          <ToggleButton v-model="saveLogo" label="Logo">
            <strong>Logo</strong>: transparent title logo
          </ToggleButton>
          <ToggleButton v-model="saveIcon" label="Icon">
            <strong>Icon</strong>: small square icon
          </ToggleButton>
        </div>
      </div>

      <div class="field-block">
        <span class="field-label">Data to save from a search result</span>
        <p class="field-sublabel">
          Turn a field off to leave it untouched by search/refresh: useful if
          you keep your own values for something and don't want them
          overwritten.
        </p>
        <div class="refresh-options">
          <ToggleButton v-model="saveDeveloper" label="Developer">
            <strong>Developer</strong>: the studio that made the game
          </ToggleButton>
          <ToggleButton v-model="savePublisher" label="Publisher">
            <strong>Publisher</strong>: who released it
          </ToggleButton>
          <ToggleButton v-model="saveSeries" label="Series">
            <strong>Series</strong>: franchise name, e.g. "Halo"
          </ToggleButton>
          <ToggleButton v-model="saveTags" label="Tags">
            <strong>Tags</strong>: Steam genre tags (RPG, Strategy, etc.)
          </ToggleButton>
          <ToggleButton v-model="saveFeatures" label="Features">
            <strong>Features</strong>: Steam categories (Co-op, Controller
            support, etc.)
          </ToggleButton>
          <ToggleButton v-model="saveDescription" label="Description">
            <strong>Description</strong>: the "About This Game" text and
            screenshots
          </ToggleButton>
          <ToggleButton v-model="saveAgeRating" label="Age rating">
            <strong>Age rating</strong>: e.g. "17+"
          </ToggleButton>
          <ToggleButton v-model="saveReleaseDate" label="Release date">
            <strong>Release date</strong>
          </ToggleButton>
          <ToggleButton v-model="saveTimeToBeat" label="Time to beat">
            <strong>Time to beat</strong>: community completion estimates from
            HowLongToBeat and IGDB
          </ToggleButton>
        </div>
      </div>

      <div v-if="error" class="form-error">{{ error }}</div>
      <div v-if="saveSuccess" class="form-success">Scan settings saved.</div>

      <button
        type="button"
        class="primary-button"
        :disabled="saving"
        @click="save"
      >
        {{ saving ? "Saving…" : "Save" }}
      </button>
    </template>
  </section>
</template>

<style scoped>
.settings-section h2 {
  margin: 0 0 12px;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  color: var(--ui-text);
}
.section-hint {
  color: var(--ui-dim);
  font-size: 0.82rem;
  line-height: 1.6;
  margin: 0 0 16px;
}
.field-block {
  margin-bottom: 20px;
}
.provider-order-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 24px;
  margin-bottom: 16px;
}
.field-label {
  display: block;
  font-size: 0.85rem;
  color: var(--ui-text);
  margin-bottom: 4px;
}
.field-sublabel {
  color: var(--ui-faint);
  font-size: 0.76rem;
  line-height: 1.5;
  margin: 0 0 12px;
}
.opted-out {
  margin-bottom: 20px;
}
.opted-out a {
  color: var(--ui-accent-text);
}
.refresh-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-error {
  color: var(--ui-error);
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-bottom: 14px;
}
.form-success {
  color: var(--ui-good);
  font-size: 13px;
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-bottom: 14px;
}
.primary-button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 11px 20px;
  font-weight: 600;
  cursor: pointer;
}
.primary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
