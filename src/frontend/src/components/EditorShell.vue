<script setup lang="ts" generic="T extends string">
import { computed, nextTick, ref, useId, watch } from "vue";
import UiModal from "./UiModal.vue";

// The shared full-editor frame: a hero with the artwork and title, a row of
// direct tabs, one bounded scrolling body and actions pinned below it. Each
// editor keeps its own fields, state and saving; this only arranges them, so
// Modern and Classic share the markup, focus order and keyboard behavior and
// differ only through the presentation tokens. Without `heading` the dialog
// keeps the plain header; without `tabs` it grows with its content.
const props = withDefaults(
  defineProps<{
    // the dialog's accessible name, shown as the hero's kicker
    title: string;
    heading?: string;
    subtitle?: string;
    cover?: string | null;
    coverLabel?: string;
    tabs?: readonly T[];
    tabsLabel?: string;
    size?: "standard" | "wide" | "editor";
    dismissible?: boolean;
  }>(),
  {
    heading: undefined,
    subtitle: undefined,
    cover: null,
    coverLabel: "Show artwork",
    tabs: () => [],
    tabsLabel: "Editor sections",
    size: "editor",
    dismissible: true,
  },
);
const tab = defineModel<T>("tab");
const emit = defineEmits<{ close: []; submit: []; cover: [] }>();

const formId = useId();
const panelId = `${formId}-panel`;
const tabId = (item: T) => `${formId}-tab-${props.tabs.indexOf(item)}`;
const bounded = computed(() => props.tabs.length > 0);
const coverFailed = ref(false);
watch(
  () => props.cover,
  () => (coverFailed.value = false),
);

// Only the tab row scrolls to keep the active tab visible on phones; moving
// the whole dialog would hide the row it is scrolling.
const tabRow = ref<HTMLElement | null>(null);
const body = ref<HTMLFormElement | null>(null);
watch(tab, () =>
  nextTick(() => {
    const row = tabRow.value;
    const active = row?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (row && active) {
      if (active.offsetLeft < row.scrollLeft)
        row.scrollLeft = active.offsetLeft - 8;
      else if (
        active.offsetLeft + active.offsetWidth >
        row.scrollLeft + row.clientWidth
      )
        row.scrollLeft =
          active.offsetLeft + active.offsetWidth - row.clientWidth + 8;
    }
    if (body.value) body.value.scrollTop = 0;
  }),
);

function onTabKey(event: KeyboardEvent) {
  const index = tab.value === undefined ? -1 : props.tabs.indexOf(tab.value);
  const last = props.tabs.length - 1;
  const next =
    event.key === "ArrowRight"
      ? index >= last
        ? 0
        : index + 1
      : event.key === "ArrowLeft"
        ? index <= 0
          ? last
          : index - 1
        : event.key === "Home"
          ? 0
          : event.key === "End"
            ? last
            : null;
  if (next === null) return;
  event.preventDefault();
  tab.value = props.tabs[next];
  void nextTick(() =>
    tabRow.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus(),
  );
}

// Scroll the body back to its start, e.g. to reveal a lookup or an error.
function scrollToTop() {
  if (body.value) body.value.scrollTop = 0;
}
defineExpose({ scrollToTop });
</script>

<template>
  <UiModal
    :title="title"
    :size="size"
    :bounded="bounded"
    :dismissible="dismissible"
    class="editor-shell"
    @close="emit('close')"
  >
    <template v-if="heading !== undefined" #header="{ titleId }">
      <div class="editor-hero">
        <button
          type="button"
          class="editor-cover"
          :class="{ empty: !cover || coverFailed }"
          :aria-label="coverLabel"
          :title="coverLabel"
          @click="emit('cover')"
        >
          <img
            v-if="cover && !coverFailed"
            :src="cover"
            alt=""
            @error="coverFailed = true"
          />
        </button>
        <div class="editor-titles">
          <h2 :id="titleId" class="editor-kicker">{{ title }}</h2>
          <p class="editor-heading">{{ heading }}</p>
          <p v-if="subtitle" class="editor-subtitle">{{ subtitle }}</p>
        </div>
      </div>
    </template>

    <div class="editor-frame" :class="{ bounded }">
      <div
        v-if="tabs.length"
        ref="tabRow"
        class="editor-tabs"
        role="tablist"
        :aria-label="tabsLabel"
        @keydown="onTabKey"
      >
        <button
          v-for="item in tabs"
          :id="tabId(item)"
          :key="item"
          type="button"
          role="tab"
          class="editor-tab"
          :class="{ active: item === tab }"
          :aria-selected="item === tab"
          :aria-controls="panelId"
          :tabindex="item === tab ? 0 : -1"
          :autofocus="item === tab"
          @click="tab = item"
        >
          {{ item }}
        </button>
      </div>
      <form
        :id="formId"
        ref="body"
        class="editor-body"
        @submit.prevent="emit('submit')"
      >
        <slot name="lead" />
        <div
          :id="panelId"
          class="editor-panel"
          :role="tabs.length ? 'tabpanel' : undefined"
          :aria-labelledby="tabs.length && tab ? tabId(tab) : undefined"
        >
          <slot />
        </div>
      </form>
      <div v-if="$slots.status" class="editor-status">
        <slot name="status" />
      </div>
    </div>

    <template #footer>
      <slot name="actions" :form-id="formId" />
    </template>
  </UiModal>
</template>

<style scoped>
.editor-hero {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
  flex: 1;
}

.editor-cover {
  flex: none;
  width: 64px;
  height: 90px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
  cursor: pointer;
}

.editor-cover img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.editor-cover.empty {
  background: linear-gradient(
    150deg,
    var(--ui-accent-soft),
    var(--ui-surface-2) 70%
  );
}

.editor-cover:hover {
  border-color: var(--ui-accent-line);
}

.editor-cover:focus-visible,
.editor-tab:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}

.editor-titles {
  flex: 1;
  min-width: 0;
}

.editor-kicker {
  margin: 0;
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 1.4;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ui-accent-text);
}

.editor-heading {
  margin: 2px 0 0;
  overflow: hidden;
  color: var(--ui-text);
  font-size: var(--ui-font-heading);
  font-weight: var(--ui-weight-heading);
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-subtitle {
  margin: 2px 0 0;
  overflow: hidden;
  color: var(--ui-faint);
  font-size: var(--ui-font-small);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.editor-frame.bounded {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}

.editor-tabs {
  flex: none;
  display: flex;
  gap: 4px;
  align-self: flex-start;
  max-width: calc(100% - 40px);
  margin: 12px 20px 0;
  padding: 4px;
  overflow-x: auto;
  border-radius: var(--ui-radius-row);
  background: var(--ui-surface-2);
  scrollbar-width: none;
}

.editor-tabs::-webkit-scrollbar {
  display: none;
}

.editor-tab {
  flex: none;
  min-height: calc(var(--ui-control-height) - 6px);
  padding: 0 14px;
  border: 0;
  border-radius: var(--ui-radius-control);
  background: transparent;
  color: var(--ui-dim);
  font: inherit;
  font-size: 0.84rem;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--ui-motion-fast) ease,
    color var(--ui-motion-fast) ease;
}

.editor-tab:hover {
  color: var(--ui-text);
}

.editor-tab.active {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.bounded .editor-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px 20px;
}

.editor-panel {
  min-width: 0;
}

.editor-status {
  flex: none;
}

.bounded .editor-status {
  padding: 0 20px 12px;
}

@media (max-width: 760px) {
  .editor-hero {
    gap: 12px;
  }

  .editor-cover {
    width: 48px;
    height: 68px;
  }

  .editor-tabs {
    max-width: calc(100% - 32px);
    margin: 10px 16px 0;
  }

  .bounded .editor-body {
    padding: 14px 16px 16px;
  }

  .bounded .editor-status {
    padding: 0 16px 10px;
  }
}
</style>
