<script setup lang="ts">
import { computed, ref, useId, watch } from "vue";
import {
  searchSettings,
  settingsSearchIndex,
  type SettingsSearchEntry,
} from "../../state/settingsRegistry";

const emit = defineEmits<{ select: [entry: SettingsSearchEntry] }>();

const id = useId();
const listId = `${id}-results`;
const query = ref("");
const expanded = ref(false);
const activeIndex = ref(0);
const input = ref<HTMLInputElement | null>(null);

// Results come from the same registry as the sidebar, so unavailable admin
// pages and inactive plugin sections never appear here.
const results = computed(() =>
  searchSettings(settingsSearchIndex.value, query.value),
);
const showPanel = computed(() => expanded.value && !!query.value.trim());
const activeId = computed(() =>
  showPanel.value && results.value.length
    ? `${id}-option-${activeIndex.value}`
    : undefined,
);

watch(results, () => {
  activeIndex.value = 0;
});

function choose(entry: SettingsSearchEntry | undefined) {
  if (!entry) return;
  query.value = "";
  expanded.value = false;
  emit("select", entry);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    if (!query.value && !expanded.value) return;
    event.preventDefault();
    event.stopPropagation();
    query.value = "";
    expanded.value = false;
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    expanded.value = true;
    const count = results.value.length;
    if (!count) return;
    const step = event.key === "ArrowDown" ? 1 : -1;
    activeIndex.value = (activeIndex.value + step + count) % count;
  } else if (event.key === "Enter") {
    if (!showPanel.value) return;
    event.preventDefault();
    choose(results.value[activeIndex.value]);
  }
}

defineExpose({ focus: () => input.value?.focus() });
</script>

<template>
  <div class="settings-search" role="search">
    <label class="settings-search-label" :for="`${id}-input`">
      Search settings
    </label>
    <input
      :id="`${id}-input`"
      ref="input"
      v-model="query"
      class="ui-field settings-search-input"
      type="search"
      role="combobox"
      autocomplete="off"
      placeholder="Search settings"
      aria-autocomplete="list"
      :aria-expanded="showPanel"
      :aria-controls="listId"
      :aria-activedescendant="activeId"
      @input="expanded = true"
      @focus="expanded = true"
      @blur="expanded = false"
      @keydown="onKeydown"
    />
    <div v-show="showPanel" class="settings-search-panel">
      <ul
        v-if="results.length"
        :id="listId"
        role="listbox"
        aria-label="Settings"
      >
        <li
          v-for="(entry, index) in results"
          :id="`${id}-option-${index}`"
          :key="entry.key"
          role="option"
          :aria-selected="index === activeIndex"
          :class="{ active: index === activeIndex }"
          @mousedown.prevent
          @mouseenter="activeIndex = index"
          @click="choose(entry)"
        >
          <span class="settings-search-result">{{ entry.label }}</span>
          <span class="settings-search-context">{{ entry.context }}</span>
        </li>
      </ul>
      <p v-else class="settings-search-empty" role="status">
        No settings match “{{ query.trim() }}”.
      </p>
    </div>
  </div>
</template>

<style scoped>
.settings-search {
  position: relative;
  width: min(100%, 280px);
}
.settings-search-label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.settings-search-input {
  width: 100%;
}
.settings-search-panel {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  right: 0;
  left: 0;
  min-width: 240px;
  padding: 6px;
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  box-shadow: var(--ui-elevation);
}
.settings-search-panel ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.settings-search-panel li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: var(--ui-radius-row);
  cursor: pointer;
}
.settings-search-panel li.active {
  background: var(--ui-accent-soft);
}
.settings-search-result {
  color: var(--ui-text);
  font-size: var(--ui-font-small);
}
.settings-search-context {
  color: var(--ui-dim);
  font-size: 11px;
}
.settings-search-empty {
  margin: 0;
  padding: 10px;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
@media (max-width: 760px) {
  .settings-search {
    width: 100%;
  }
}
</style>
