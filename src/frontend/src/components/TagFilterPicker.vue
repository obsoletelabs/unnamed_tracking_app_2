<script setup lang="ts">
// One searchable picker for including and excluding tags & genres, in place
// of a chip for every tag (and another "Not" chip for every tag), which grew
// to several screens with Steam player tags. Choices come from
// tagFilterOptions, so a search finds a tag by any spelling it merges and
// every row says what it also matches. Modern and Classic share it.
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import SegmentedTabs from "./SegmentedTabs.vue";
import { tagKey, tagSpellingKey, type TagFilterOption } from "../utils/genres";

type Mode = "include" | "exclude";

const props = defineProps<{
  options: TagFilterOption[];
  included: string[];
  excluded: string[];
}>();
const emit = defineEmits<{
  toggle: [value: string, mode: Mode];
  remove: [value: string, mode: Mode];
}>();

const MODES = [
  {
    value: "include",
    label: "Include",
    title: "Show games with any included tag",
  },
  {
    value: "exclude",
    label: "Exclude",
    title: "Hide games with any excluded tag",
  },
];
const mode = ref<Mode>("include");
const otherMode = computed<Mode>(() =>
  mode.value === "include" ? "exclude" : "include",
);
const query = ref("");
const open = ref(false);
const activeIndex = ref(0);
const announcement = ref("");
const listId = useId();
let blurTimer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(blurTimer));

const searchable = computed(() =>
  props.options.map((option) => {
    const names = [option.value, ...option.spellings];
    return {
      option,
      key: tagKey(option.value),
      texts: names.map((name) => name.toLowerCase()),
      keys: names.map(tagSpellingKey),
    };
  }),
);
const chosenKeys = computed(() => ({
  include: new Set(props.included.map(tagKey)),
  exclude: new Set(props.excluded.map(tagKey)),
}));
function stateOf(key: string): Mode | null {
  // An exclusion wins when an older link names a tag in both lists.
  if (chosenKeys.value.exclude.has(key)) return "exclude";
  if (chosenKeys.value.include.has(key)) return "include";
  return null;
}

interface Row {
  option: TagFilterOption;
  index: number;
  state: Mode | null;
  detail: string;
}

function detail(option: TagFilterOption): string {
  const parts: string[] = [];
  if (option.spellings.length)
    parts.push(`Also matches ${option.spellings.join(", ")}`);
  if (option.broader.length)
    parts.push(`Counts as ${option.broader.join(", ")}`);
  return parts.join(" · ");
}

// Matches any spelling by text or by spelling key, so "sci fi" finds
// "Sci-fi" and "steam: race" finds Racing. Names that start with the search
// come first within each group.
const groups = computed(() => {
  const text = query.value.trim().toLowerCase();
  const key = tagSpellingKey(query.value);
  const ranked = searchable.value
    .map((entry) => {
      if (!text) return { entry, rank: 0 };
      if (
        entry.texts.some((t) => t.startsWith(text)) ||
        (key && entry.keys.some((k) => k.startsWith(key)))
      )
        return { entry, rank: 0 };
      if (
        entry.texts.some((t) => t.includes(text)) ||
        (key && entry.keys.some((k) => k.includes(key)))
      )
        return { entry, rank: 1 };
      return null;
    })
    .filter((match) => match !== null)
    .sort((a, b) => a.rank - b.rank)
    .map(({ entry }) => entry);
  let index = 0;
  const rows = (shared: boolean): Row[] =>
    ranked
      .filter((entry) => entry.option.shared === shared)
      .map((entry) => ({
        option: entry.option,
        index: index++,
        state: stateOf(entry.key),
        detail: detail(entry.option),
      }));
  return [
    { id: "genres", label: "Genres", rows: rows(true) },
    { id: "tags", label: "More tags", rows: rows(false) },
  ].filter((group) => group.rows.length);
});
const flat = computed(() => groups.value.flatMap((group) => group.rows));
watch(query, () => {
  activeIndex.value = 0;
});

function choose(row: Row, chosenMode: Mode) {
  const value = row.option.value;
  emit("toggle", value, chosenMode);
  announcement.value =
    row.state === chosenMode
      ? `Removed ${value}`
      : `${chosenMode === "include" ? "Included" : "Excluded"} ${value}`;
}

async function scrollToActive() {
  await nextTick();
  document
    .getElementById(`${listId}-${activeIndex.value}`)
    ?.scrollIntoView({ block: "nearest" });
}

function onKeydown(event: KeyboardEvent) {
  const total = flat.value.length;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!open.value) {
      open.value = true;
      activeIndex.value = 0;
    } else if (total) {
      const step = event.key === "ArrowDown" ? 1 : -1;
      activeIndex.value = (activeIndex.value + step + total) % total;
    }
    void scrollToActive();
  } else if (event.key === "Enter" && open.value && total) {
    event.preventDefault();
    const row = flat.value[Math.min(activeIndex.value, total - 1)]!;
    choose(row, event.shiftKey ? otherMode.value : mode.value);
  } else if (event.key === "Escape" && (open.value || query.value)) {
    event.preventDefault();
    open.value = false;
    query.value = "";
  }
}

function onFocus() {
  clearTimeout(blurTimer);
  open.value = true;
}

function onBlur() {
  blurTimer = setTimeout(() => {
    open.value = false;
  }, 150);
}

function remove(value: string, chosenMode: Mode) {
  emit("remove", value, chosenMode);
  announcement.value = `Removed ${value}`;
}
</script>

<template>
  <div class="tag-picker">
    <div class="tag-picker-controls">
      <div class="tag-picker-search">
        <input
          v-model="query"
          type="text"
          class="tag-picker-input"
          role="combobox"
          aria-label="Tags & genres"
          aria-autocomplete="list"
          :aria-expanded="open"
          :aria-controls="listId"
          :aria-activedescendant="
            open && flat.length ? `${listId}-${activeIndex}` : undefined
          "
          :placeholder="`Tags & genres (${options.length})`"
          @input="open = true"
          @keydown="onKeydown"
          @focus="onFocus"
          @blur="onBlur"
        />
        <div v-if="open" class="tag-picker-menu">
          <div
            :id="listId"
            class="tag-picker-list"
            role="listbox"
            aria-multiselectable="true"
            aria-label="Tags & genres"
          >
            <div
              v-for="group in groups"
              :key="group.id"
              role="group"
              :aria-labelledby="`${listId}-${group.id}`"
            >
              <div :id="`${listId}-${group.id}`" class="tag-picker-group">
                {{ group.label }}
              </div>
              <div
                v-for="row in group.rows"
                :id="`${listId}-${row.index}`"
                :key="row.option.value"
                class="tag-picker-option"
                role="option"
                :aria-selected="row.state !== null"
                :class="{
                  highlighted: activeIndex === row.index,
                  included: row.state === 'include',
                  excluded: row.state === 'exclude',
                }"
                @pointerdown.prevent
                @pointermove="activeIndex = row.index"
                @click="choose(row, mode)"
              >
                <span class="tag-picker-option-text">
                  <span class="tag-picker-option-name">
                    <span v-if="row.state" class="tag-picker-state">
                      {{ row.state === "include" ? "Included" : "Excluded" }}
                    </span>
                    {{ row.option.value }}
                  </span>
                  <span v-if="row.detail" class="tag-picker-option-detail">
                    {{ row.detail }}
                  </span>
                </span>
                <span class="tag-picker-count"
                  >{{ row.option.count
                  }}<span class="tag-picker-sr">
                    {{ row.option.count === 1 ? "game" : "games" }}</span
                  ></span
                >
              </div>
            </div>
          </div>
          <div v-if="!flat.length" class="tag-picker-empty">
            No tags or genres match.
          </div>
          <div class="tag-picker-hint" aria-hidden="true">
            Enter: {{ mode }} · Shift+Enter: {{ otherMode }}
          </div>
        </div>
      </div>
      <SegmentedTabs
        :options="MODES"
        :model-value="mode"
        aria-label="Tag choice"
        @update:model-value="mode = $event as Mode"
      />
    </div>
    <div
      v-if="included.length || excluded.length"
      class="tag-picker-chosen"
      role="group"
      aria-label="Chosen tags & genres"
    >
      <button
        v-for="tag in included"
        :key="`include-${tag}`"
        type="button"
        class="tag-picker-chip"
        :aria-label="`Remove included ${tag}`"
        @click="remove(tag, 'include')"
      >
        {{ tag }} <span aria-hidden="true">✕</span>
      </button>
      <button
        v-for="tag in excluded"
        :key="`exclude-${tag}`"
        type="button"
        class="tag-picker-chip excluded"
        :aria-label="`Remove excluded ${tag}`"
        @click="remove(tag, 'exclude')"
      >
        Not {{ tag }} <span aria-hidden="true">✕</span>
      </button>
    </div>
    <span class="tag-picker-sr" aria-live="polite">{{ announcement }}</span>
  </div>
</template>

<style scoped>
.tag-picker {
  position: relative;
  display: flex;
  flex: 1 1 320px;
  flex-direction: column;
  max-width: 760px;
  gap: 8px;
  min-width: 0;
}
.tag-picker-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.tag-picker-search {
  position: relative;
  flex: 1 1 200px;
  min-width: 0;
}
.tag-picker-input {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--ui-control-height);
  padding: 0 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
}
.tag-picker-input::placeholder {
  color: var(--ui-faint);
}
.tag-picker-input:focus {
  border-color: var(--ui-accent);
}
.tag-picker-menu {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  display: flex;
  flex-direction: column;
  max-height: min(340px, 60vh);
  box-sizing: border-box;
  padding: 4px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-popover);
  box-shadow: var(--ui-elevation);
  z-index: 50;
}
.tag-picker-list {
  min-height: 0;
  overflow-y: auto;
}
.tag-picker-group {
  padding: 8px 10px 4px;
  color: var(--ui-dim);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.tag-picker-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: var(--ui-control-height);
  box-sizing: border-box;
  padding: 6px 10px;
  border-radius: 6px;
  color: var(--ui-text);
  font-size: 13px;
  cursor: pointer;
}
.tag-picker-option.highlighted {
  background: var(--ui-surface-2);
}
.tag-picker-option.included {
  color: var(--ui-accent-text);
}
.tag-picker-option.excluded {
  color: var(--ui-danger);
}
.tag-picker-option-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}
.tag-picker-option-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag-picker-option-detail {
  overflow: hidden;
  color: var(--ui-dim);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag-picker-state {
  margin-right: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.tag-picker-count {
  flex-shrink: 0;
  color: var(--ui-dim);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.tag-picker-empty,
.tag-picker-hint {
  padding: 8px 10px;
  color: var(--ui-dim);
  font-size: 12px;
}
.tag-picker-hint {
  border-top: 1px solid var(--ui-border-soft);
  margin-top: 4px;
  font-size: 11px;
}
.tag-picker-chosen {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag-picker-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  min-height: 28px;
  padding: 0 10px 0 12px;
  border: 1px solid var(--ui-accent-line);
  border-radius: 999px;
  background: var(--ui-accent-soft);
  color: var(--ui-accent-text);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.tag-picker-chip.excluded {
  border-color: color-mix(in srgb, var(--ui-danger) 45%, transparent);
  background: var(--ui-danger-soft);
  color: var(--ui-danger);
}
.tag-picker-chip span {
  font-size: 10px;
}
.tag-picker-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
@media (max-width: 760px) {
  .tag-picker-input {
    font-size: 16px;
  }
  .tag-picker-chip {
    min-height: 36px;
  }
}
</style>
