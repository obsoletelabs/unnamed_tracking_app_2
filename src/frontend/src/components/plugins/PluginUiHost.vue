<script setup lang="ts">
import {
  readPluginAppearance,
  observePluginAppearance,
} from "../../services/pluginAppearance";
import PluginField from "./PluginField.vue";
import { pluginShortcutEvent } from "../../services/pluginShortcutBridge";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  buildInitialValues,
  dispatchPluginAction,
  downloadPluginDocument,
  openExternalDestination,
  PluginActionError,
  pluginExternalDestination,
  reserveExternalTab,
  fetchPluginSettings,
  savedFieldValues,
  validateField,
  type PluginUiDocument,
  type UiAction,
  type UiField,
  type UiValues,
} from "../../services/pluginUi";
import { useConfirm } from "../../state/dialog";

const props = defineProps<{
  document: PluginUiDocument;
  tableData?: Record<string, Record<string, string | number | boolean>[]>;
  pageId?: string;
  embedded?: boolean;
  inline?: boolean;
  context?: Record<string, string | number | boolean>;
  widgetConfig?: UiValues;
}>();
const emit = defineEmits<{
  action: [action: UiAction, values: UiValues];
  save: [values: UiValues];
  navigate: [pageId: string];
}>();
const activePage = ref(props.pageId ?? props.document.pages[0]?.id ?? "");
const values = ref<UiValues>(buildInitialValues(props.document));
const submitted = ref(false);