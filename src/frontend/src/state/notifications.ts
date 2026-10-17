// Episode/season/movie notifications from the server: the app's one
// notification source. Persisted server-side, so they can be read/unread.
import { ref, watch } from "vue";
import { currentUser } from "./auth";
import { notificationDestination } from "../utils/notificationPresentation";
import {
  fetchMediaNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "../services/notifications";
import type { MediaNotification } from "../services/notifications";

export const mediaNotifications = ref<MediaNotification[]>([]);
export const mediaUnread = ref(0);
let generation = 0;
watch(
  () => currentUser.value?.id,
  () => {
    generation++;
    mediaNotifications.value = [];
    mediaUnread.value = 0;
  },
  { flush: "sync" },
);

let pendingRefresh: {
  account: string | undefined;
  done: Promise<void>;
} | null = null;

// Mounted components (the sidebar, the bell) ask for the same list in the
// same tick. `shared` lets them join one request instead of each sending its
// own; callers reacting to a change (opening the bell, a dev action) still
// start a fresh request so they never read an older snapshot.
export function refreshMediaNotifications(
  options: { shared?: boolean } = {},
): Promise<void> {
  const account = currentUser.value?.id;
  if (options.shared && pendingRefresh && pendingRefresh.account === account)
    return pendingRefresh.done;
  const request = ++generation;
  const done = (async () => {
    try {
      const res = await fetchMediaNotifications();
      if (request !== generation || currentUser.value?.id !== account) return;
      mediaNotifications.value = res.items;
      mediaUnread.value = res.unread;
    } catch {
      // the badge simply doesn't change this tick
    }
  })();
  const entry = { account, done };
  pendingRefresh = entry;
  void done.finally(() => {
    if (pendingRefresh === entry) pendingRefresh = null;
  });
  return done;
}

export async function readMediaNotification(id: string) {
  const request = generation;
  const n = mediaNotifications.value.find((m) => m.id === id);
  if (!n || n.read) return;
  n.read = true;
  mediaUnread.value = Math.max(0, mediaUnread.value - 1);
  try {
    await markNotificationRead(id);
  } catch {
    if (request !== generation) return;
    n.read = false;
    mediaUnread.value += 1;
  }
}

export async function readAllMediaNotifications() {
  const request = generation;
  const before = mediaNotifications.value.map((n) => n.read);
  mediaNotifications.value.forEach((n) => (n.read = true));
  const unread = mediaUnread.value;
  mediaUnread.value = 0;
  try {
    await markAllNotificationsRead();
  } catch {
    if (request !== generation) return;
    mediaNotifications.value.forEach((n, i) => (n.read = before[i]));
    mediaUnread.value = unread;
  }
}

export function mediaNotificationRoute(n: MediaNotification): string {
  return notificationDestination(n) ?? "/notifications";
}
