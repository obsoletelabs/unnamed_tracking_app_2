<script setup lang="ts">
// The full page for one notification story: the latest authorised state with
// facts, rich text, images and declared actions. The bell stays compact; this
// is where the rich experience lives. Both UI styles share this one layout.
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import AppTopBar from "../components/AppTopBar.vue";
import NotificationLiveLinks from "../components/NotificationLiveLinks.vue";
import RichNodes from "../components/RichNodes.vue";
import {
  fetchStory,
  fetchStoryByLink,
  runStoryAction,
} from "../services/notificationStories";
import type { StoryAction, StoryPage } from "../services/notificationStories";
import { useConfirm } from "../state/dialog";
import { formatStamp, safeHref } from "../utils/richNodes";

const route = useRoute();
const confirm = useConfirm();
const page = ref<StoryPage | null>(null);
const loading = ref(true);
const error = ref("");
const result = ref("");
const running = ref<string | null>(null);
const failedImages = ref(new Set<number>());

const view = computed(() => page.value?.view ?? null);

async function load() {
  loading.value = true;
  error.value = "";
  failedImages.value = new Set();
  try {
    const token = route.params.token;
    page.value =
      typeof token === "string"
        ? await fetchStoryByLink(token)
        : await fetchStory(String(route.params.id));
  } catch {
    page.value = null;
    error.value =
      "This update is not available. It may have been removed, or it belongs to another account.";
  } finally {
    loading.value = false;
  }
}

async function run(action: StoryAction) {
  if (!page.value) return;
  const danger = action.style === "danger";
  if (
    danger &&
    !(await confirm({
      title: action.label,
      message: `Run "${action.label}"? This cannot be undone.`,
      confirmLabel: action.label,
      danger: true,
    }))
  )
    return;
  running.value = action.id;
  result.value = "";
  try {
    const outcome = await runStoryAction(page.value.storyId, action.id, danger);
    result.value = outcome.message ?? `${action.label} completed.`;
    await load();
  } catch (failure) {
    result.value =
      failure instanceof Error ? failure.message : "The action failed.";
  } finally {
    running.value = null;
  }
}

function hrefFor(action: StoryAction) {
  return safeHref(action.href);
}

watch(() => [route.params.id, route.params.token], load);
onMounted(load);
</script>

<template>
  <main class="ui-page story-page" :aria-busy="loading">
    <AppTopBar>
      <RouterLink to="/notifications" class="ui-btn ui-btn-secondary"
        >All notifications</RouterLink
      >
    </AppTopBar>
    <div class="ui-content narrow">
      <p v-if="loading && !page" class="ui-state" role="status">Loading…</p>
      <p v-else-if="error" class="ui-state error" role="alert">{{ error }}</p>
      <article v-else-if="page" class="story" aria-labelledby="story-title">
        <header class="story-head">
          <p class="status" :class="page.severity">
            <span class="visually-hidden">Status:</span>{{ page.status }}
          </p>
          <h1 id="story-title">{{ page.title }}</h1>
          <p class="meta">
            Last updated
            <time :datetime="new Date(page.updatedAt * 1000).toISOString()">{{
              formatStamp(page.updatedAt)
            }}</time>
            · {{ page.source === "host" ? "Tracking app" : page.source }}
          </p>
          <p v-if="view?.stale" class="stale" role="status">
            This update is no longer current.
          </p>
        </header>

        <template v-if="view">
          <section class="body" aria-label="Summary">
            <RichNodes :nodes="view.summary" />
          </section>

          <section v-if="view.images.length" class="images" aria-label="Images">
            <figure v-for="image in view.images" :key="image.index">
              <img
                v-if="image.url && !failedImages.has(image.index)"
                :src="image.url"
                :alt="image.alt"
                loading="lazy"
                decoding="async"
                referrerpolicy="no-referrer"
                @error="failedImages.add(image.index)"
              />
              <svg
                v-else
                class="fallback"
                viewBox="0 0 320 180"
                role="img"
                aria-label="Image unavailable"
              >
                <rect width="320" height="180" />
                <path d="M110 120l30-38 22 26 16-18 32 30z" />
                <circle cx="124" cy="66" r="12" />
              </svg>
              <figcaption v-if="image.alt">{{ image.alt }}</figcaption>
            </figure>
          </section>

          <section
            v-for="(block, index) in view.blocks"
            :key="index"
            class="body"
            aria-label="Details"
          >
            <RichNodes :nodes="block" />
          </section>

          <section v-if="view.facts.length" aria-labelledby="facts-heading">
            <h2 id="facts-heading">Details</h2>
            <dl class="facts">
              <div v-for="fact in view.facts" :key="fact.key">
                <dt>{{ fact.label }}</dt>
                <dd>
                  <time
                    v-if="fact.iso && fact.ts !== undefined"
                    :datetime="fact.iso"
                    >{{ formatStamp(fact.ts) }}</time
                  >
                  <template v-else>{{ fact.text }}</template>
                </dd>
              </div>
            </dl>
          </section>

          <section
            v-if="view.actions.length"
            class="actions"
            role="group"
            aria-label="Actions"
          >
            <template v-for="action in view.actions" :key="action.id">
              <button
                v-if="action.kind === 'action'"
                type="button"
                class="ui-btn"
                :class="
                  action.style === 'danger'
                    ? 'ui-btn-danger-soft'
                    : action.style === 'primary'
                      ? 'ui-btn-primary'
                      : 'ui-btn-secondary'
                "
                :disabled="running !== null"
                :aria-busy="running === action.id"
                @click="run(action)"
              >
                {{ action.label }}
              </button>
              <a
                v-else-if="hrefFor(action)?.kind === 'external'"
                class="ui-btn ui-btn-secondary"
                :href="hrefFor(action)!.href"
                target="_blank"
                rel="noopener noreferrer nofollow"
                >{{ action.label
                }}<span class="visually-hidden"> (opens in a new tab)</span></a
              >
              <RouterLink
                v-else-if="hrefFor(action)?.kind === 'route'"
                class="ui-btn ui-btn-secondary"
                :to="hrefFor(action)!.href"
                >{{ action.label }}</RouterLink
              >
            </template>
          </section>
          <p class="result" role="status" aria-live="polite">{{ result }}</p>

          <p v-if="view.attribution" class="attribution">
            Source:
            <a
              v-if="safeHref(view.attribution.url)?.kind === 'external'"
              :href="safeHref(view.attribution.url)!.href"
              target="_blank"
              rel="noopener noreferrer nofollow"
              >{{ view.attribution.label }}</a
            >
            <template v-else>{{ view.attribution.label }}</template>
          </p>
        </template>
        <section v-else class="body" aria-label="Message">
          <p
            v-for="(line, index) in (page.text ?? '').split('\n\n')"
            :key="index"
          >
            {{ line }}
          </p>
        </section>

        <NotificationLiveLinks :story-id="page.storyId" />
      </article>
    </div>
  </main>
</template>

<style scoped>
.story {
  display: grid;
  gap: var(--ui-space-5);
}
.story-head {
  display: grid;
  gap: var(--ui-space-2);
}
h1 {
  margin: 0;
  font-size: var(--ui-font-title);
  font-weight: var(--ui-weight-title);
  overflow-wrap: anywhere;
}
h2 {
  margin: 0 0 var(--ui-space-3);
  font-size: var(--ui-font-heading);
  font-weight: var(--ui-weight-heading);
}
.status {
  justify-self: start;
  margin: 0;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: var(--ui-font-small);
  font-weight: 650;
  color: var(--ui-info);
  background: var(--ui-info-soft);
}
.status.warning {
  color: var(--ui-warning);
  background: var(--ui-warning-soft);
}
.status.error {
  color: var(--ui-danger);
  background: var(--ui-danger-soft);
}
.meta,
.attribution,
.result {
  margin: 0;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.stale {
  margin: 0;
  font-weight: 650;
  color: var(--ui-warning);
}
.body {
  line-height: 1.65;
  overflow-wrap: anywhere;
  max-width: var(--ui-reading-width);
}
.body :deep(p),
.body :deep(ul),
.body :deep(ol),
.body :deep(blockquote),
.body :deep(pre) {
  margin: 0 0 var(--ui-space-3);
}
.body :deep(blockquote) {
  padding-left: var(--ui-space-3);
  border-left: 3px solid var(--ui-accent-line);
  color: var(--ui-dim);
}
.body :deep(pre) {
  overflow: auto;
  padding: var(--ui-space-3);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
}
.body :deep(a) {
  color: var(--ui-accent-text);
}
.images {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
  gap: var(--ui-space-3);
}
figure {
  margin: 0;
  display: grid;
  gap: var(--ui-space-2);
}
figure img,
.fallback {
  width: 100%;
  height: auto;
  max-height: 360px;
  object-fit: cover;
  border-radius: var(--ui-radius-card);
  background: var(--ui-surface-2);
}
.fallback rect {
  fill: var(--ui-surface-2);
}
.fallback path,
.fallback circle {
  fill: var(--ui-border-strong);
}
figcaption {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr));
  gap: var(--ui-space-3);
  margin: 0;
}
.facts div {
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-border-soft);
  border-radius: var(--ui-radius-row);
  background: var(--ui-surface);
}
dt {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-2);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
</style>
