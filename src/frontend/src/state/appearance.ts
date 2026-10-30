import { ref, watch } from "vue";
import { fetchAppearanceSettings } from "../services/appearanceSettings";
import type { AppearanceSettings } from "../services/appearanceSettings";
import { currentUser } from "./auth";

// loaded once at app boot (see router/index.ts), not per-card, GameCard.vue
// renders hundreds of times and reads this shared ref rather than each
// making its own request
export const appearanceSettings = ref<AppearanceSettings | null>(null);
export const appearanceLoaded = ref(false);
let generation = 0;

export function resetAppearanceSettings(): void {
  generation++;
  appearanceSettings.value = null;
  appearanceLoaded.value = false;
}

export function rememberAppearanceSettings(settings: AppearanceSettings): void {
  if (settings.user_id !== currentUser.value?.id) return;
  generation++;
  appearanceSettings.value = settings;
  appearanceLoaded.value = true;
}

watch(() => currentUser.value?.id, resetAppearanceSettings, { flush: "sync" });

let pending: {
  account: string | undefined;
  generation: number;
  done: Promise<void>;
} | null = null;

export function loadAppearanceSettings(): Promise<void> {
  const requestGeneration = generation;
  const accountId = currentUser.value?.id;
  if (
    pending?.account === accountId &&
    pending?.generation === requestGeneration
  )
    return pending.done;
  const done = (async () => {
    try {
      const result = await fetchAppearanceSettings();
      if (
        requestGeneration !== generation ||
        currentUser.value?.id !== accountId
      )
        return;
      appearanceSettings.value = result;
    } catch {
      // Optional decorations keep their defaults during a failed read.
    } finally {
      if (
        requestGeneration === generation &&
        currentUser.value?.id === accountId
      )
        appearanceLoaded.value = true;
    }
  })();
  const entry = { account: accountId, generation: requestGeneration, done };
  pending = entry;
  void done.finally(() => {
    if (pending === entry) pending = null;
  });
  return done;
}
