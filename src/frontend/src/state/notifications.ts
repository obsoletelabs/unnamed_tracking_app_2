// Episode/season/movie notifications from the server: the app's one
// notification source. Persisted server-side, so they can be read/unread.
import { ref, watch } from "vue";
import { currentUser } from "./auth";
import { notificationDestination } from "../utils/notificationPresentation";
import {
  fetchMediaNotifications,
  fetchMediaUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
} from "../services/notifications";
import type { MediaNotification } from "../services/notifications";

export const mediaNotifications = ref<MediaNotification[]>([]);
export const mediaUnread = ref(0);
export const mediaNotificationsLoading = ref(false);
export const mediaNextOffset = ref<number | null>(null);
let generation = 0;
let countGeneration = 0;
watch(
  () => currentUser.value?.id,
  () => {
    generation++;
    countGeneration++;
    mediaNextOffset.value = null;
    mediaNotificationsLoading.value = false;
    mediaNotifications.value = [];
    mediaUnread.value = 0;
  },
  { flush: "sync" },
);

let pendingCount: { account: string | undefined; done: Promise<void> } | null =
  null;
export function refreshMediaUnread(
  options: { shared?: boolean } = {},
): Promise<void> {
  const account = currentUser.value?.id;
  if (!account) return Promise.resolve();
  if (options.shared && pendingCount && pendingCount.account === account)
    return pendingCount.done;
  const token = ++countGeneration;
  const done = (async () => {
    try {
      const count = await fetchMediaUnreadCount();
      if (token === countGeneration && account === currentUser.value?.id)
        mediaUnread.value = count;
    } catch {
      /* Keep the last known badge during an outage. */
    }
  })();
  const entry = { account, done };
  pendingCount = entry;
  void done.finally(() => {
    if (pendingCount === entry) pendingCount = null;
  });
  return done;
}

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
  const countRequest = ++countGeneration;
  mediaNotificationsLoading.value = true;
  const done = (async () => {
    try {
      const res = await fetchMediaNotifications(10, { include_summary: false });
      if (request !== generation || currentUser.value?.id !== account) return;
      mediaNotifications.value = res.items;
      mediaNextOffset.value = res.nextOffset;
      if (countRequest === countGeneration) mediaUnread.value = res.unread;
    } catch {
      // Keep already loaded items during a transient outage.
    } finally {
      if (request === generation) mediaNotificationsLoading.value = false;
    }
  })();
  const entry = { account, done };
  pendingRefresh = entry;
  void done.finally(() => {
    if (pendingRefresh === entry) pendingRefresh = null;
  });
  return done;
}

export async function loadMoreMediaNotifications(): Promise<void> {
  const offset = mediaNextOffset.value;
  const account = currentUser.value?.id;
  if (offset === null || mediaNotificationsLoading.value) return;
  const request = generation;
  const countRequest = ++countGeneration;
  mediaNotificationsLoading.value = true;
  try {
    const res = await fetchMediaNotifications(10, {
      include_summary: false,
      offset,
    });
    if (request !== generation || account !== currentUser.value?.id) return;
    const seen = new Set(mediaNotifications.value.map((item) => item.id));
    mediaNotifications.value = [
      ...mediaNotifications.value,
      ...res.items.filter((item) => !seen.has(item.id)),
    ];
    mediaNextOffset.value = res.nextOffset;
    if (countRequest === countGeneration) mediaUnread.value = res.unread;
  } catch {
    /* A later scroll or explicit load can retry this page. */
  } finally {
    if (request === generation && account === currentUser.value?.id)
      mediaNotificationsLoading.value = false;
  }
}

export async function readMediaNotification(id: string) {
  const request = generation;
  const n = mediaNotifications.value.find((m) => m.id === id);
  if (!n || n.read) return;
  countGeneration++;
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
  countGeneration++;
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
