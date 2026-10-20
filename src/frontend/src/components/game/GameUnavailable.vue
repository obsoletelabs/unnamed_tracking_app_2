<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { gameLinkStates, type GameLinkFailure } from "../../utils/gameLink";

const props = defineProps<{ kind: GameLinkFailure }>();
defineEmits<{ retry: [] }>();
const presentation = computed(() => gameLinkStates[props.kind]);
const heading = ref<HTMLHeadingElement | null>(null);
async function focusHeading() {
  await nextTick();
  heading.value?.focus({ preventScroll: true });
}
onMounted(focusHeading);
watch(() => props.kind, focusHeading);
</script>

<template>
  <section class="game-unavailable" aria-labelledby="game-unavailable-title">
    <div class="state-mark" aria-hidden="true">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
      >
        <path
          d="M7 6h10a3 3 0 0 1 3 3l1 7a2.5 2.5 0 0 1-4.3 2L15 16H9l-1.7 2A2.5 2.5 0 0 1 3 16l1-7a3 3 0 0 1 3-3Z"
        />
        <path
          d="M7 9v5M4.5 11.5h5M15 10h.01M18 13h.01"
          stroke-linecap="round"
        />
      </svg>
    </div>
    <p class="eyebrow">Your game library</p>
    <h1 id="game-unavailable-title" ref="heading" tabindex="-1">
      {{ presentation.title }}
    </h1>
    <p class="description">{{ presentation.description }}</p>
    <div class="state-actions">
      <button
        v-if="kind === 'unavailable'"
        class="ui-btn ui-btn-primary"
        type="button"
        @click="$emit('retry')"
      >
        Try again
      </button>
      <RouterLink
        v-if="kind === 'signed-out'"
        to="/login"
        class="ui-btn ui-btn-primary"
        >Sign in</RouterLink
      >
      <RouterLink
        to="/games"
        class="ui-btn"
        :class="
          kind === 'not-found' || kind === 'invalid'
            ? 'ui-btn-primary'
            : 'ui-btn-secondary'
        "
        >Back to games</RouterLink
      >
    </div>
  </section>
</template>

<style scoped>
.game-unavailable {
  width: min(560px, calc(100% - 32px));
  box-sizing: border-box;
  margin: clamp(32px, 10vh, 112px) auto;
  padding: clamp(28px, 5vw, 52px);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-lg, 20px);
  background: var(--ui-surface);
  text-align: center;
  color: var(--ui-text);
}
.state-mark {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  margin: 0 auto 24px;
  border-radius: 22px;
  background: var(--ui-accent-soft);
  color: var(--ui-accent);
}
.state-mark svg {
  width: 40px;
  height: 40px;
}
.eyebrow {
  margin: 0 0 12px;
  color: var(--ui-dim);
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: clamp(1.6rem, 4vw, 2rem);
  line-height: 1.2;
  text-wrap: balance;
}
h1:focus {
  outline: none;
}
.description {
  margin: 18px 0 28px;
  color: var(--ui-dim);
  font-size: 1rem;
  line-height: 1.7;
  text-wrap: pretty;
}
.state-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}
.state-actions .ui-btn {
  min-height: 44px;
  text-decoration: none;
}
@media (max-width: 440px) {
  .state-actions > * {
    width: 100%;
    justify-content: center;
  }
}
</style>
