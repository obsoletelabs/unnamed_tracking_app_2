<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  onErrorCaptured,
  ref,
  watch,
} from "vue";
import type {
  PluginActionContext,
  PluginUiDocument,
  UiAction,
  UiValues,
} from "../../services/pluginUi";
import {
  dispatchPluginAction,
  openExternalDestination,
  pluginExternalDestination,
  reserveExternalTab,
} from "../../services/pluginUi";
import { pluginRequestError } from "../../services/apiError";
import { approvePluginAction } from "../../services/pluginUi";
import {
  nativePluginComponents,
  nativePluginFailures,
  ensureNativePlugin,
} from "../../state/pluginNative";
import { activePluginDocuments } from "../../state/pluginExtensions";
// The form/table renderer (including secret fields) is only needed for an
// active declarative contribution, not to mount the application shell.
const PluginUiHost = defineAsyncComponent(() => import("./PluginUiHost.vue"));

const props = defineProps<{
  pluginId: string;
  document: PluginUiDocument;
  pageId: string;
  context?: Record<string, string | number | boolean>;
  embedded?: boolean;
  inline?: boolean;
  actionContext?: PluginActionContext;
  widgetConfig?: UiValues;
}>();
const emit = defineEmits<{ navigate: [pageId: string]; failed: [] }>();

const failed = ref(false);
const active = computed(() =>
  Boolean(activePluginDocuments.value[props.pluginId]),
);
const component = computed(
  () => nativePluginComponents.value[`${props.pluginId}:${props.pageId}`],
);
const nativeLoading = ref(false);
let nativeGeneration = 0;
watch(
  () => [active.value, props.pluginId, props.document.native_frontend] as const,
  async ([enabled, pluginId, native]) => {
    const generation = ++nativeGeneration;
    if (!enabled || !native) {
      nativeLoading.value = false;
      return;
    }
    nativeLoading.value = true;
    try {
      await ensureNativePlugin(pluginId);
    } finally {
      if (generation === nativeGeneration) nativeLoading.value = false;
    }
  },
  { immediate: true },
);
const activationFailed = computed(() =>
  Boolean(nativePluginFailures.value[props.pluginId]),
);

watch(
  () => `${props.pluginId}:${props.pageId}:${String(component.value)}`,
  () => (failed.value = false),
);

onErrorCaptured(() => {
  failed.value = true;
  emit("failed");
  return false;
});

async function save(values: UiValues) {
  const response = await fetch(
    `/api/plugins/${encodeURIComponent(props.pluginId)}/settings`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    },
  );
  if (!response.ok)
    throw await pluginRequestError(
      response,
      "Plugin settings could not be saved",
    );
}

async function run(action: UiAction, values: UiValues) {
  const tab = reserveExternalTab(action);
  let destination: string | null = null;
  try {
    const result = await dispatchPluginAction(
      props.pluginId,
      action.id,
      values,
      props.actionContext,
      Boolean(action.confirmation),
    );
    destination = pluginExternalDestination(action, result);
  } finally {
    openExternalDestination(destination, tab);
  }
}

const nativeHost = computed(() => ({
  runAction: async (actionId: string, values: Record<string, unknown> = {}) => {
    const action = props.document.actions.find((item) => item.id === actionId);
    if (!action) throw new Error("Plugin action not found.");
    if (!approvePluginAction(action, window.confirm))
      return { cancelled: true };
    return dispatchPluginAction(
      props.pluginId,
      actionId,
      values,
      props.actionContext,
      Boolean(action.confirmation),
    );
  },
}));
</script>

<template>
  <p
    v-if="active && (failed || activationFailed)"
    class="plugin-failure"
    role="status"
  >
    This plugin contribution failed and was removed from the page.
  </p>
  <component
    :is="component"
    v-else-if="active && component"
    :key="`${pluginId}:${pageId}:${actionContext?.resource_id ?? ''}`"
    :plugin-id="pluginId"
    :page-id="pageId"
    :context="context ?? {}"
    :host="nativeHost"
    :widget-config="widgetConfig ?? {}"
  />
  <p v-else-if="active && nativeLoading" role="status">Loading plugin…</p>
  <PluginUiHost
    v-else-if="active"
    :document="document"
    :page-id="pageId"
    :context="context"
    :embedded="embedded"
    :inline="inline"
    :widget-config="widgetConfig"
    @save="save"
    @action="run"
    @navigate="emit('navigate', $event)"
  />
</template>

<style scoped>
.plugin-failure {
  padding: 12px;
  border: 1px solid var(--ui-error);
  border-radius: var(--ui-radius-control);
  color: var(--ui-error);
}
</style>
