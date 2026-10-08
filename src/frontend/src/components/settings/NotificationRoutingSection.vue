<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import {
  fetchNotificationRouting,
  setNotificationProviderEnabled,
} from "../../services/notifications";
import type {
  NotificationRoutingSettings,
  NotificationRoutingType,
  NotificationRoutingDestination,
} from "../../services/notifications";
import type { Preferences } from "../../services/preferences";

const props = defineProps<{ prefs: Preferences; loaded: boolean }>();
const emit = defineEmits<{ change: [changes: Partial<Preferences>] }>();
const routing = ref<NotificationRoutingSettings | null>(null);
const error = ref("");
const expanded = ref<Record<string, boolean>>({});
const busy = ref(false);
let mounted = true;
onUnmounted(() => {
  mounted = false;
});

async function reload() {
  try {
    const result = await fetchNotificationRouting();
    if (mounted) routing.value = result;
  } catch (reason) {
    if (mounted)
      error.value =
        reason instanceof Error ? reason.message : "Failed to load routing.";
  }
}
onMounted(reload);

function typeEnabled(type: NotificationRoutingType) {
  const legacy = props.prefs[type.preference_key as keyof Preferences];
  return (
    legacy !== false &&
    props.prefs.notification_types[type.event_type] !== false
  );
}
function setType(type: NotificationRoutingType, enabled: boolean) {
  const changes: Partial<Preferences> = {
    notification_types: {
      ...props.prefs.notification_types,
      [type.event_type]: enabled,
    },
  };
  if (
    typeof props.prefs[type.preference_key as keyof Preferences] === "boolean"
  )
    Object.assign(changes, { [type.preference_key]: enabled });
  emit("change", changes);
}
function choice(
  type: NotificationRoutingType,
  destination: NotificationRoutingDestination,
) {
  return (
    props.prefs.notification_routes[type.event_type]?.[destination.id] ?? {
      enabled: true,
      urgency: "normal" as const,
    }
  );
}
function setRoute(
  type: NotificationRoutingType,
  destination: NotificationRoutingDestination,
  changes: Partial<{ enabled: boolean; urgency: "normal" | "critical" }>,
) {
  emit("change", {
    notification_routes: {
      ...props.prefs.notification_routes,
      [type.event_type]: {
        ...props.prefs.notification_routes[type.event_type],
        [destination.id]: { ...choice(type, destination), ...changes },
      },
    },
  });
}
function personalEnabled(destination: NotificationRoutingDestination) {
  return props.prefs.notification_destinations[destination.id] !== false;
}
async function setProvider(providerId: string, enabled: boolean) {
  busy.value = true;
  error.value = "";
  try {
    await setNotificationProviderEnabled(providerId, enabled);
    if (mounted) await reload();
  } catch (reason) {
    if (mounted)
      error.value =
        reason instanceof Error ? reason.message : "Failed to save provider.";
  } finally {
    if (mounted) busy.value = false;
  }
}
function availability(destination: NotificationRoutingDestination) {
  if (!destination.active || !destination.available)
    return "Inactive · requires reactivation";
  if (!destination.provider_enabled || !destination.enabled)
    return "Provider disabled";
  if (!personalEnabled(destination)) return "Disabled for your account";
  return destination.shared_configuration
    ? "Public release facts only"
    : "Routing enabled";
}
</script>

<template>
  <div class="notification-routing">
    <h3>Notification types</h3>
    <p class="hint">
      Switch a type on or off, then expand it to choose where it goes. Turning
      it off keeps your routing choices.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!routing && !error" class="hint">Loading providers…</p>
    <template v-if="routing">
      <article
        v-for="type in routing.types"
        :key="type.event_type"
        class="type-card"
      >
        <div class="type-header">
          <ToggleButton
            :model-value="typeEnabled(type)"
            :label="type.label"
            :disabled="!loaded"
            @update:model-value="setType(type, $event)"
          >
            <strong>{{ type.label }}</strong>
          </ToggleButton>
          <button
            type="button"
            class="expand"
            :aria-label="`Configure ${type.label}`"
            :aria-expanded="!!expanded[type.event_type]"
            :aria-controls="`route-${type.event_type}`"
            @click="expanded[type.event_type] = !expanded[type.event_type]"
          >
            Providers {{ expanded[type.event_type] ? "▴" : "▾" }}
          </button>
        </div>
        <p class="hint description">{{ type.description }}</p>
        <div
          v-if="expanded[type.event_type]"
          :id="`route-${type.event_type}`"
          class="route-list"
        >
          <p v-if="!typeEnabled(type)" class="hint">
            This type is off. Choices below are saved for when you enable it.
          </p>
          <div
            v-for="destination in routing.destinations"
            :key="destination.id"
            class="destination-row"
          >
            <div>
              <ToggleButton
                :model-value="
                  destination.eligible_types.includes(type.event_type) &&
                  choice(type, destination).enabled
                "
                :label="`${type.label} / ${destination.provider_name}`"
                :aria-label="`${type.label} / ${destination.provider_name}`"
                :disabled="
                  !loaded ||
                  !destination.eligible_types.includes(type.event_type) ||
                  !destination.active ||
                  !destination.available
                "
                @update:model-value="
                  setRoute(type, destination, { enabled: $event })
                "
              >
                <strong>{{ destination.provider_name }}</strong>
              </ToggleButton>
              <p class="hint">
                {{ destination.trust }} ·
                {{ destination.context === "internal" ? "In-app" : "External" }}
                · {{ availability(destination) }}
              </p>
              <p
                v-if="!destination.eligible_types.includes(type.event_type)"
                class="restriction"
              >
                This destination does not meet this type's trust or server
                policy requirements.
              </p>
            </div>
            <label class="urgency">
              <span>Delivery urgency</span>
              <select
                :aria-label="`${type.label} / ${destination.provider_name} urgency`"
                :value="choice(type, destination).urgency"
                :disabled="
                  !loaded ||
                  !destination.eligible_types.includes(type.event_type) ||
                  !destination.active ||
                  !destination.available
                "
                @change="
                  setRoute(type, destination, {
                    urgency: ($event.target as HTMLSelectElement).value as
                      'normal' | 'critical',
                  })
                "
              >
                <option value="normal">Normal</option>
                <option value="critical">Critical</option>
              </select>
              <span v-if="!destination.critical_supported" class="hint"
                >Normal delivery only today; a Critical request stays
                saved.</span
              >
            </label>
          </div>
          <p
            v-for="provider in routing.providers.filter(
              (p) => !routing?.destinations.some((d) => d.provider_id === p.id),
            )"
            :key="provider.id"
            class="hint"
          >
            {{ provider.name }}: enable it under Your providers to create a
            destination.
          </p>
          <p class="hint">
            Critical requests sound or Do Not Disturb override only where
            supported.
            {{
              type.required_trust === "SECURE"
                ? "Security alerts still require a secure destination at either urgency."
                : "Urgency does not change what a destination may receive."
            }}
          </p>
        </div>
      </article>

      <section id="user-notification-providers" class="provider-section">
        <h3>Your providers</h3>
        <p class="hint">
          Enable external providers for your account here. Per-type choices
          above apply within these settings.
        </p>
        <article
          v-for="provider in routing.providers"
          :key="provider.id"
          class="provider-card"
        >
          <h4>{{ provider.name }}</h4>
          <p v-if="provider.configuration_scope === 'internal'" class="hint">
            Your authenticated, secure inbox. Choose its notification types
            above.
          </p>
          <template v-else>
            <ToggleButton
              :model-value="provider.enabled"
              :label="`Enable ${provider.name} for my account`"
              :aria-label="`Enable ${provider.name} for my account`"
              :disabled="busy || !provider.available"
              @update:model-value="setProvider(provider.id, $event)"
            >
              Enable for my account
            </ToggleButton>
            <p class="hint">
              {{
                provider.available
                  ? "Uses administrator-configured delivery. Personal endpoint setup will be added with destination enrollment."
                  : "Provider unavailable. Your choices are retained; reinstall does not reactivate old destinations."
              }}
            </p>
          </template>
          <div
            v-for="destination in routing.destinations.filter(
              (d) => d.provider_id === provider.id,
            )"
            :key="destination.id"
            class="personal-destination"
          >
            <ToggleButton
              :model-value="personalEnabled(destination)"
              :label="`Use ${destination.provider_name} destination`"
              :aria-label="`Use ${destination.provider_name} destination`"
              :disabled="
                !loaded || !destination.active || !destination.available
              "
              @update:model-value="
                emit('change', {
                  notification_destinations: {
                    ...prefs.notification_destinations,
                    [destination.id]: $event,
                  },
                })
              "
            >
              Use this
              {{ destination.context === "internal" ? "inbox" : "destination" }}
            </ToggleButton>
            <p class="hint">
              {{ destination.trust }} · {{ availability(destination) }}
            </p>
            <p v-if="destination.shared_configuration" class="hint">
              Shared server webhook. It receives generic public release facts,
              never sign-in alerts. Personal media sharing requires separate
              consent for a concrete webhook.
            </p>
          </div>
        </article>
        <p v-if="routing.providers.length === 1" class="hint">
          No external providers are registered. Email and browser push setup
          will appear when those providers are available.
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
h3 {
  margin: 22px 0 8px;
  font-size: 0.95rem;
}
h4 {
  margin: 0 0 12px;
  font-size: 0.87rem;
}
.hint,
.restriction,
.error {
  font-size: 0.78rem;
  line-height: 1.6;
  margin: 5px 0;
  color: var(--ui-dim);
}
.restriction {
  color: var(--ui-warning);
}
.error {
  color: var(--ui-error);
}
.type-card,
.provider-card {
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  padding: 14px;
  margin: 10px 0;
  background: var(--ui-surface);
}
.type-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.type-header :deep(.toggle-button) {
  margin: 0;
}
.expand {
  flex-shrink: 0;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 6px 9px;
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  cursor: pointer;
}
.description {
  margin-left: 44px;
}
.route-list {
  border-top: 1px solid var(--ui-border);
  margin-top: 12px;
  padding-top: 10px;
}
.destination-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(130px, 180px);
  gap: 18px;
  padding: 12px 0;
}
.destination-row :deep(.toggle-button) {
  margin-bottom: 4px;
}
.urgency {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 0.78rem;
}
.urgency select {
  color: var(--ui-text);
  background: var(--ui-surface-alt);
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  padding: 7px;
  width: 100%;
  font: inherit;
}
.personal-destination {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--ui-border);
}
@media (max-width: 600px) {
  .destination-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .description {
    margin-left: 0;
  }
}
</style>
