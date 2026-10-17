<script setup lang="ts">
import type { TraceStage } from "../../services/notificationDiagnostics";

defineProps<{ stages: TraceStage[]; heading: string }>();

const STAGE_LABELS: Record<string, string> = {
  ingestion: "Ingestion",
  grouping: "Grouping",
  timing: "Timing",
  eligibility: "Eligibility",
  capability: "Capability",
  delivery: "Delivery",
};

function summary(stage: TraceStage): string[] {
  const out: string[] = [];
  const d = stage.detail;
  if (typeof d.status === "string") out.push(`status ${d.status}`);
  if (typeof d.strategy === "string") out.push(`strategy ${d.strategy}`);
  if (typeof d.phase === "string") out.push(`phase ${d.phase}`);
  if (typeof d.projection === "string") out.push(`content ${d.projection}`);
  if (typeof d.trust === "string") out.push(`trust ${d.trust}`);
  if (typeof d.release_at === "number")
    out.push(`released ${new Date(d.release_at * 1000).toLocaleString()}`);
  if (typeof d.renderer === "string") out.push(`renderer ${d.renderer}`);
  if (Array.isArray(d.attempts))
    out.push(
      (d.attempts as { number: number; code: string }[])
        .map((a) => `#${a.number} ${a.code}`)
        .join(", "),
    );
  return out;
}
</script>

<template>
  <section class="trace" :aria-label="heading">
    <h4>{{ heading }}</h4>
    <ol>
      <li v-for="(stage, index) in stages" :key="index">
        <span class="stage">{{
          STAGE_LABELS[stage.stage] ?? stage.stage
        }}</span>
        <span class="code">{{ stage.code }}</span>
        <p class="label">{{ stage.label }}</p>
        <p v-if="summary(stage).length" class="facts">
          {{ summary(stage).join(" · ") }}
        </p>
        <p class="next"><strong>Next:</strong> {{ stage.action }}</p>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.trace h4 {
  margin: 14px 0 6px;
  font-size: 0.86rem;
  color: var(--ui-text);
}
ol {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
li {
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  font-size: 0.82rem;
}
.stage {
  font-weight: 700;
  color: var(--ui-text);
  margin-right: 8px;
}
.code {
  font-family: var(--ui-font-mono, monospace);
  color: var(--ui-accent-text);
}
p {
  margin: 4px 0 0;
  color: var(--ui-dim);
  line-height: 1.5;
}
.next {
  color: var(--ui-faint);
}
</style>
