<script setup lang="ts">
// "Related games": DLC, expansions, remakes, remasters, ports and other
// editions linked to this game, readable from both ends. The links come from
// the identity relationships (Advanced › Versions edits them); this panel
// only lists them so each related page is one click away. Loading is best
// effort: without it the page simply has no panel.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { fetchIdentitySources } from "../../services/gameIdentity";
import {
  relatedGameLinks,
  type RelatedGameLink,
} from "../../utils/identityTabs";

const props = defineProps<{ gameId: string }>();

const links = ref<RelatedGameLink[]>([]);
let request: AbortController | null = null;

async function load(): Promise<void> {
  request?.abort();
  const mine = (request = new AbortController());
  try {
    const data = await fetchIdentitySources(props.gameId, mine.signal);
    if (!mine.signal.aborted)
      links.value = relatedGameLinks(data, props.gameId);
  } catch {
    if (!mine.signal.aborted) links.value = [];
  }
}

watch(() => props.gameId, load, { immediate: true });
onBeforeUnmount(() => request?.abort());

const partOf = computed(() =>
  links.value.filter((link) => link.direction === "outgoing"),
);
const others = computed(() =>
  links.value.filter((link) => link.direction === "incoming"),
);
</script>

<template>
  <section
    v-if="links.length"
    class="related-games"
    aria-labelledby="related-games-title"
  >
    <h2 id="related-games-title">Related games</h2>
    <ul class="links">
      <li v-for="link in [...partOf, ...others]" :key="link.relationshipId">
        <RouterLink
          :to="`/games/${link.gameId}`"
          class="link"
          :class="link.direction"
        >
          <span class="kind">{{ link.label }}</span>
          <span class="related-title">{{ link.title }}</span>
          <span class="arrow" aria-hidden="true">→</span>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.related-games {
  margin: 0 0 16px;
}
h2 {
  font-size: 1.05rem;
  margin: 0 0 8px;
}
.links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.link {
  align-items: center;
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  display: inline-flex;
  gap: 8px;
  max-width: 100%;
  min-height: var(--ui-control-height);
  padding: 6px 12px;
  text-decoration: none;
  transition: border-color var(--ui-motion-fast) var(--ui-motion-easing);
}
.link:hover,
.link:focus-visible {
  border-color: var(--ui-accent-line);
  outline: none;
}
.link:focus-visible {
  box-shadow: 0 0 0 2px var(--ui-accent-soft);
}
.kind {
  background: var(--ui-surface-2);
  border-radius: 999px;
  color: var(--ui-dim);
  flex: none;
  font-size: 0.72rem;
  font-weight: 700;
  padding: 2px 8px;
}
.link.outgoing .kind {
  background: var(--ui-accent-soft);
  color: var(--ui-accent-text);
}
.related-title {
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.arrow {
  color: var(--ui-faint);
  flex: none;
}
</style>
