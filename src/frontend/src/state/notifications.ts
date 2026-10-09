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

export async function refreshMediaNotifications() {
  const request = ++generation;
  const account = currentUser.value?.id;
  try {
    const res = await fetchMediaNotifications();
    if (request !== generation || currentUser.value?.id !== account) return;
    mediaNotifications.value = res.items;
    mediaUnread.value = res.unread;
  } catch {
    // the badge simply doesn't change this tick
  }
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
