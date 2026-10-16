<script setup lang="ts">
// One artwork role (cover or banner) in the full game editor: the saved
// artwork, what is staged to replace it, and every way to choose: a local
// file, an image link or an offered search result. Choosing only stages the
// image in the editor; Save uploads or fetches it. The newest explicit choice
// wins, so a chosen file is never replaced by search results arriving later.
import { computed, ref, useId, watch } from "vue";
import type { StagedArtwork } from "../../composables/useStagedArtwork";
import {
  allowSameFileReselection,
  fileFromEvent,
} from "../../composables/useStagedArtwork";

const props = defineProps<{
  label: string;
  shape: "tall" | "wide";
  // the saved artwork, absent while adding a game
  current: string | null;
  staged: StagedArtwork | null;
  candidates: string[];
  disabled?: boolean;
}>();
const emit = defineEmits<{
  file: [file: File];
  url: [url: string, source: "link" | "provider"];
  reset: [];
}>();

const headingId = useId();
const link = ref("");
const linkError = ref<string | null>(null);
const previewFailed = ref(false);

const preview = computed(() => {
  const choice = props.staged;
  if (!choice) return props.current;
  return choice.source === "file" ? choice.preview : choice.url;
});
watch(preview, () => (previewFailed.value = false));

const SOURCE_LABELS = {
  file: "Local file",
  link: "Link",
  provider: "Search",
} as const;
const tag = computed(() =>
  props.staged
    ? SOURCE_LABELS[props.staged.source]
    : props.current
      ? "Current"
      : "None",
);
const state = computed(() => {
  const choice = props.staged;
  if (!choice)
    return props.current
      ? "The saved artwork stays unless you choose another."
      : "No artwork chosen yet.";
  if (choice.source === "file")
    return `${choice.file.name} uploads when you save.`;
  return choice.source === "link"
    ? "This link is fetched when you save."
    : "This search result is fetched when you save.";
});

function onFile(event: Event) {
  const file = fileFromEvent(event);
  if (!file) return;
  linkError.value = null;
  emit("file", file);
}

function useLink() {
  const url = link.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) {
    linkError.value = "Enter an http or https image link.";
    return;
  }
  linkError.value = null;
  emit("url", url, "link");
  link.value = "";
}

const isPicked = (url: string) =>
  props.staged?.source === "provider" && props.staged.url === url;
</script>

<template>
  <section class="art-picker" :aria-labelledby="headingId">
    <p :id="headingId" class="editor-section">{{ label }}</p>
    <div class="art-row">
      <div class="art-preview" :class="shape">
        <img v-if="preview && !previewFailed" :src="preview" alt="" @error="previewFailed = true" />
        <span class="art-tag">{{ tag }}</span>
      </div>
      <div class="art-actions">
        <p class="art-state" role="status">{{ state }}</p>
        <label class="ui-btn ui-btn-secondary art-upload">
          Upload a file
          <input type="file" accept="image/*" class="art-file" :aria-label="`Upload a ${label.toLowerCase()} file`"
            :disabled="disabled" @click="allowSameFileReselection" @change="onFile" />
        </label>
        <div class="art-link">
          <!-- text, not url: a half-typed link in a hidden tab must not block Save -->
          <input v-model="link" type="text" inputmode="url" autocomplete="off" class="editor-input"
            :aria-label="`${label} image link`" placeholder="Paste an image link" :disabled="disabled"
            @keydown.enter.prevent="useLink" />
          <button type="button" class="ui-btn ui-btn-secondary" :disabled="disabled || !link.trim()" @click="useLink">
            Use
          </button>
        </div>
        <p v-if="linkError" class="art-error" role="alert">{{ linkError }}</p>
        <button v-if="staged" type="button" class="ui-btn ui-btn-ghost ui-btn-sm art-reset" :disabled="disabled"
          @click="emit('reset')">
          {{ current ? "Keep current" : "Remove choice" }}
        </button>
      </div>
    </div>
    <div v-if="candidates.length" class="art-options" role="group" :aria-label="`${label} choices`">
      <button v-if="current" type="button" class="art-tile" :class="[shape, { chosen: !staged }]"
        :aria-pressed="!staged" :disabled="disabled" @click="emit('reset')">
        <img :src="current" alt="Current artwork" loading="lazy" />
        <span class="art-tag">Current</span>
      </button>
      <button v-for="url in candidates" :key="url" type="button" class="art-tile"
        :class="[shape, { chosen: isPicked(url) }]" :aria-pressed="isPicked(url)" :disabled="disabled"
        @click="emit('url', url, 'provider')">
        <img :src="url" alt="Search result" loading="lazy" />
        <span class="art-tag">Search</span>
      </button>
    </div>
  </section>
</template>

<style scoped src="../../styles/shared/editorForm.css"></style>
<style scoped>
.art-picker {
  min-width: 0;
}

.art-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.art-preview,
.art-tile {
  position: relative;
  flex: none;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
}

.art-preview img,
.art-tile img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.art-preview.tall {
  width: 84px;
  height: 120px;
}

.art-preview.wide {
  width: 176px;
  height: 66px;
}

.art-tag {
  position: absolute;
  left: 5px;
  bottom: 5px;
  padding: 1px 6px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--ui-bg) 82%, transparent);
  color: var(--ui-text);
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.art-actions {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.art-state {
  margin: 0;
  color: var(--ui-dim);
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}

.art-upload {
  position: relative;
}

.art-upload:focus-within {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}

.art-file {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.art-link {
  display: flex;
  gap: 6px;
  width: 100%;
  max-width: 420px;
}

.art-link .editor-input {
  min-width: 0;
}

.art-error {
  margin: 0;
  color: var(--ui-error);
  font-size: 0.78rem;
}

.art-options {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  padding-bottom: 4px;
  overflow-x: auto;
}

.art-tile {
  padding: 0;
  border-width: 2px;
  border-color: transparent;
  cursor: pointer;
}

.art-tile.tall {
  width: 72px;
  height: 104px;
}

.art-tile.wide {
  width: 168px;
  height: 60px;
}

.art-tile:hover:not(:disabled) {
  border-color: var(--ui-border-strong);
}

.art-tile.chosen {
  border-color: var(--ui-accent);
}

.art-tile:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
}

@media (max-width: 480px) {
  .art-row {
    flex-direction: column;
  }
}
</style>
