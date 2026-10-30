import { ref, watch } from "vue";
import { currentUser } from "./auth";
import { fetchInboxCount } from "../services/media";

// shared across the sidebar badge and the Inbox page itself, the sidebar
// stays mounted across navigation, so it needs a way to learn about changes
// made on the Inbox page without re-fetching on every render
export const inboxCount = ref(0);

let generation = 0;
watch(
  () => currentUser.value?.id,
  () => {
    generation++;
    inboxCount.value = 0;
  },
  { flush: "sync" },
);

export async function refreshInboxCount() {
  const request = ++generation;
  try {
    const count = await fetchInboxCount();
    if (request === generation) inboxCount.value = count;
  } catch {
    // badge just doesn't update this tick, not worth surfacing an error
    // for a sidebar count
  }
}
