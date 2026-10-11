<script setup lang="ts">
import { computed, ref } from "vue";
import ToggleButton from "./ToggleButton.vue";
import PluginProviderConfigurationForm from "./PluginProviderConfigurationForm.vue";
import {
  configurePluginDestination,
  removePluginDestination,
  sendDestinationTest,
} from "../../services/notifications";
import type {
  NotificationRoutingDestination,
  NotificationRoutingProvider,
} from "../../services/notifications";
import type { Preferences } from "../../services/preferences";
import type { UiValue } from "../../services/pluginUi";

const props = defineProps<{
  provider: NotificationRoutingProvider;
  destinations: NotificationRoutingDestination[];
  prefs: Preferences;
  loaded: boolean;
}>();
const emit = defineEmits<{
  changed: [];
  change: [changes: Partial<Preferences>];
}>();
const editing = ref<NotificationRoutingDestination | "new" | null>(null);
const kind = ref("");
const label = ref("");
const shareMedia = ref(false);
const busy = ref(false);
const error = ref("");
const status = ref("");
const showInactive = ref(false);
const removing = ref<string | null>(null);
const kinds = computed(() => props.provider.definition?.destinations ?? []);
const declaration = computed(() =>
  kinds.value.find((item) => item.kind === kind.value),
);
const inactiveCount = computed(
  () => props.destinations.filter((d) => !d.active).length,
);
const listed = computed(() =>
  props.destinations
    .filter((d) => d.active || showInactive.value)
    .sort((a, b) => Number(b.active) - Number(a.active)),
);
const canAdd = computed(
  () =>
    props.provider.available &&
    props.loaded &&
    (props.provider.definition?.features.multiple_destinations ||
      !props.destinations.some(
        (d) => d.plugin_owned_configuration && !d.configuration_retired,
      )),
);
function edit(destination: NotificationRoutingDestination | "new") {
  editing.value = destination;
  kind.value =
    destination === "new" ? (kinds.value[0]?.kind ?? "") : destination.kind;
  label.value = destination === "new" ? "" : (destination.label ?? "");
  shareMedia.value = false;
  error.value = "";
}
async function perform(operation: () => Promise<string>) {
  busy.value = true;
  error.value = status.value = "";
  try {
    status.value = await operation();
    emit("changed");
  } catch (reason) {
    error.value =
      reason instanceof Error
        ? reason.message
        : "Could not configure this provider.";
    emit("changed"); // Failed saves may have retired old routing before the callback.
  } finally {
    busy.value = false;
  }
}
async function save(values: Record<string, UiValue>) {
  if (!editing.value) return;
  const destination = editing.value;
  await perform(async () => {
    await configurePluginDestination(
      {
        provider_id: props.provider.id,
        kind: kind.value,
        label: label.value,
        values,
        share_followed_media: shareMedia.value,
      },
      destination === "new" ? undefined : destination.id,
    );
    editing.value = null;
    return "Destination configured. Previous notifications are not replayed.";
  });
}
async function remove(destination: NotificationRoutingDestination) {
  await perform(async () => {
    const result = await removePluginDestination(destination.id);
    removing.value = null;
    return result.plugin_cleanup_complete
      ? "Destination removed and plugin cleanup confirmed. History is retained."
      : "Routing removed. Plugin credential cleanup could not be confirmed; check its storage when available.";
  });
}
</script>

<template>
  <section
    :aria-label="`${provider.name} destinations`"
    class="plugin-destinations"
  >
    <p class="hint">
      This plugin manages its own credentials and transport. It receives only
      the notification content approved for each destination.
    </p>
    <p
      v-if="provider.configuration_available && !provider.available"
      class="hint"
    >
      Server configuration is required before adding or reactivating
      destinations.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="status" class="hint" role="status">{{ status }}</p>
    <article
      v-for="destination in listed"
      :key="destination.id"
      class="destination"
    >
      <h5>{{ destination.label || destination.kind }}</h5>
      <p class="hint">
        {{ destination.trust }} · External ·
        {{ destination.active ? "Configured" : "Inactive" }}
      </p>
      <ToggleButton
        v-if="destination.active"
        :model-value="prefs.notification_destinations[destination.id] !== false"
        :label="`Use ${destination.label || destination.kind}`"
        :disabled="busy || !loaded || !destination.available"
        @update:model-value="
          emit('change', {
            notification_destinations: {
              ...prefs.notification_destinations,
              [destination.id]: $event,
            },
          })
        "
        >Use this destination</ToggleButton
      >
      <p v-if="destination.trust === 'PUBLIC'" class="hint">
        {{
          destination.media_disclosure_confirmed
            ? "Richer followed-media announcements confirmed for this revision."
            : "Public release facts only; your follow choice is not shared."
        }}
      </p>
      <div class="actions">
        <button
          v-if="destination.active"
          :disabled="
            busy ||
            !destination.available ||
            !destination.enabled ||
            !destination.provider_enabled ||
            prefs.notification_destinations[destination.id] === false
          "
          @click="
            perform(async () => {
              await sendDestinationTest(destination.id);
              return 'Example notification queued using this destination’s configured credentials.';
            })
          "
        >
          Send test notification
        </button>
        <button
          v-if="
            destination.plugin_owned_configuration &&
            !destination.configuration_retired &&
            provider.available
          "
          :disabled="busy"
          @click="edit(destination)"
        >
          {{
            destination.active
              ? "Edit configuration"
              : "Reactivate with configuration"
          }}
        </button>
        <button
          v-if="
            destination.plugin_owned_configuration &&
            !destination.configuration_retired
          "
          :disabled="busy"
          @click="removing = destination.id"
        >
          Remove
        </button>
      </div>
      <div v-if="removing === destination.id" class="confirmation">
        <p>
          Remove {{ destination.label || destination.kind }}? Queued deliveries
          will be cancelled. History and routing preferences remain.
        </p>
        <button :disabled="busy" @click="remove(destination)">
          Confirm removal
        </button>
        <button :disabled="busy" @click="removing = null">
          Keep destination
        </button>
      </div>
    </article>
    <button
      v-if="inactiveCount"
      :aria-expanded="showInactive"
      @click="showInactive = !showInactive"
    >
      {{ showInactive ? "Hide" : "Show" }} inactive destinations ({{
        inactiveCount
      }})
    </button>
    <button v-if="!editing && canAdd" :disabled="busy" @click="edit('new')">
      Add destination
    </button>
    <div v-if="editing" class="configuration">
      <h5>
        {{ editing === "new" ? "Add destination" : "Configure destination" }}
      </h5>
      <label v-if="editing === 'new' && kinds.length > 1"
        >Destination type
        <select v-model="kind" :disabled="busy" @change="shareMedia = false">
          <option
            v-for="choice in kinds"
            :key="choice.kind"
            :value="choice.kind"
          >
            {{ choice.label }} · {{ choice.privacy }}
          </option>
        </select>
      </label>
      <label
        >Label<input v-model="label" maxlength="80" :disabled="busy"
      /></label>
      <PluginProviderConfigurationForm
        v-if="declaration"
        :key="`${kind}:${editing === 'new' ? 'new' : editing.id}`"
        :fields="declaration.fields"
        :editing="editing !== 'new'"
        :busy="busy"
        @save="save"
      >
        <label v-if="declaration.privacy === 'PUBLIC'" class="consent"
          ><input v-model="shareMedia" type="checkbox" :disabled="busy" />
          I explicitly allow this destination to receive release titles,
          season/episode/release facts and the fact that I selected or followed
          them. Account identifiers, watch history/status, ratings and security
          content are excluded. It remains PUBLIC; consent applies only to this
          saved revision.
        </label>
      </PluginProviderConfigurationForm>
      <button :disabled="busy" @click="editing = null">Cancel</button>
    </div>
  </section>
</template>

<style scoped>
.plugin-destinations {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.destination,
.configuration {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 12px;
  min-width: 0;
}
h5 {
  margin: 0 0 8px;
  font-size: 14px;
}
.hint,
.error,
.consent {
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.hint {
  color: var(--ui-dim);
}
.error {
  color: var(--ui-error);
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
button,
select,
input:not([type="checkbox"]) {
  background: var(--ui-surface);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 8px 12px;
  font: inherit;
  max-width: 100%;
  box-sizing: border-box;
}
label {
  display: grid;
  gap: 6px;
  margin-block: 12px;
}
.consent {
  display: flex;
  align-items: start;
}
.consent input {
  flex-shrink: 0;
  margin-top: 5px;
}
button {
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
