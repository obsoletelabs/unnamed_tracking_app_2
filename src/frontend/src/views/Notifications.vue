<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRouter } from "vue-router";
import AppTopBar from "../components/AppTopBar.vue";
import SegmentedTabs from "../components/SegmentedTabs.vue";
import type { SegmentOption } from "../components/SegmentedTabs.vue";
import {
  fetchMediaNotifications,
  markNotificationRead,
  markNotificationUnread,
  markAllNotificationsRead,
  dismissMediaNotification,
  deleteMediaNotification,
} from "../services/notifications";
import type {
  MediaNotification,
  InboxFilter,
  InboxPage,
} from "../services/notifications";
import { refreshMediaNotifications } from "../state/notifications";
import { useKeptAlive } from "../utils/useKeptAlive";
import { useConfirm } from "../state/dialog";
import { currentUser } from "../state/auth";
import {
  readPluginReminders,
  pluginReminderRevision,
} from "../state/pluginNotifications";
import {
  notificationPresentation,
  notificationDestination,
  groupNotifications,
} from "../utils/notificationPresentation";

const FILTERS: { key: InboxFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "episodes", label: "Episodes" },
  { key: "seasons", label: "Seasons" },
  { key: "releases", label: "Releases" },
  { key: "security", label: "Security" },
  { key: "plugins", label: "Plugins" },
];
const router = useRouter();
const confirm = useConfirm();
const items = ref<MediaNotification[]>([]);
const summary = ref<Omit<InboxPage, "items">>({
  total: 0,
  unread: 0,
  counts: {},
  nextOffset: null,
  sources: [],
});
const loading = ref(true);
const pending = ref(false);
const error = ref<string | null>(null);
const filter = ref<InboxFilter>("all");
const search = ref("");
const source = ref("");
const openId = ref<string | null>(null);
const expandedGroups = ref(new Set<string>());
const reminders = ref<Awaited<ReturnType<typeof readPluginReminders>>>([]);
let generation = 0;
let reminderGeneration = 0;
let searchTimer: ReturnType<typeof setTimeout> | undefined;
async function loadReminders() {
  const request = ++reminderGeneration;
  const account = currentUser.value?.id;
  const values = await readPluginReminders();
  if (request === reminderGeneration && currentUser.value?.id === account)
    reminders.value = values;
}
watch(pluginReminderRevision, () => void loadReminders(), { immediate: true });
const shownReminders = computed(() =>
  filter.value === "all" || filter.value === "plugins"
    ? reminders.value.filter((r) =>
        `${r.label} ${r.description}`
          .toLowerCase()
          .includes(search.value.toLowerCase()),
      )
    : [],
);
async function load(more = false) {
  const request = ++generation;
  const account = currentUser.value?.id;
  loading.value = true;
  error.value = null;
  try {
    const page = await fetchMediaNotifications(50, {
      category: filter.value,
      search: search.value,
      source: source.value,
      offset: more ? (summary.value.nextOffset ?? 0) : 0,
    });
    if (request !== generation || currentUser.value?.id !== account) return;
    items.value = more
      ? [
          ...new Map(
            [...items.value, ...page.items].map((n) => [n.id, n]),
          ).values(),
        ]
      : page.items;
    summary.value = page;
  } catch (e) {
    if (request === generation)
      error.value =
        e instanceof Error ? e.message : "Failed to load notifications.";
  } finally {
    if (request === generation) loading.value = false;
  }
}
onMounted(() => void load());
useKeptAlive(() => void load());
watch([filter, source], () => {
  clearTimeout(searchTimer);
  void load();
});
watch(search, () => {
  generation++;
  loading.value = true;
  summary.value.nextOffset = null;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => void load(), 250);
});
watch(
  () => currentUser.value?.id,
  () => {
    generation++;
    reminderGeneration++;
    items.value = [];
    reminders.value = [];
  },
);
onUnmounted(() => {
  generation++;
  reminderGeneration++;
  clearTimeout(searchTimer);
});
const unreadCount = computed(() => summary.value.unread);
const filterOptions = computed<SegmentOption[]>(() =>
  FILTERS.map((f) => ({
    value: f.key,
    label: f.label,
    count: summary.value.counts[f.key] ?? 0,
  })),
);
const grouped = computed(() => {
  const days = new Map<string, ReturnType<typeof groupNotifications>>();
  for (const cluster of groupNotifications(items.value)) {
    const existing = days.get(cluster.day);
    if (existing) existing.push(cluster);
    else days.set(cluster.day, [cluster]);
  }
  return [...days.entries()].sort((a, b) => b[0].localeCompare(a[0]));
});
function dayLabel(key: string): string {
  const date = new Date(`${key}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (date.getTime() === today.getTime()) return "Today";
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.getTime() === yesterday.getTime()) return "Yesterday";
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}
function exactTime(unix: number): string {
  return new Date(unix * 1000).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
function timeOnly(unix: number): string {
  return new Date(unix * 1000).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}
const TYPE_LABEL: Record<string, string> = {
  game: "Game",
  movie: "Movie",
  tv: "TV show",
  anime: "Anime",
  plugin: "Plugin",
  system: "Account",
};
function toggleGroup(key: string) {
  const next = new Set(expandedGroups.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedGroups.value = next;
}
async function mutate(action: () => Promise<unknown>) {
  if (pending.value) return;
  const account = currentUser.value?.id;
  pending.value = true;
  error.value = null;
  try {
    await action();
    if (currentUser.value?.id !== account) return;
    await load();
    await refreshMediaNotifications();
  } catch (e) {
    if (currentUser.value?.id !== account) return;
    const message =
      e instanceof Error ? e.message : "Failed to update notification.";
    // A grouped operation may have succeeded for some members before a failure.
    await load();
    await refreshMediaNotifications();
    error.value = message;
  } finally {
    pending.value = false;
  }
}
async function setRead(n: MediaNotification, read: boolean) {
  await mutate(() =>
    read ? markNotificationRead(n.id) : markNotificationUnread(n.id),
  );
}
async function readAll() {
  await mutate(markAllNotificationsRead);
}
async function dismiss(n: MediaNotification) {
  await mutate(() => dismissMediaNotification(n.id));
}
async function remove(n: MediaNotification) {
  const ok = await confirm({
    message:
      "Delete this notification and cancel its unsent deliveries? A delivery already in progress may finish.",
    confirmLabel: "Delete",
  });
  if (ok) await mutate(() => deleteMediaNotification(n.id));
}
async function groupRead(members: MediaNotification[]) {
  await mutate(async () => {
    for (const n of members.filter((n) => !n.read))
      await markNotificationRead(n.id);
  });
}
async function open(n: MediaNotification) {
  const destination = notificationDestination(n);
  if (!destination) return;
  if (!n.read) await setRead(n, true);
  await router.push(destination);
}
function toggleOpen(n: MediaNotification) {
  openId.value = openId.value === n.id ? null : n.id;
}
</script>

<template>
  <main class="ui-page">
    <AppTopBar>
      <SegmentedTabs
        :options="filterOptions"
        :model-value="filter"
        aria-label="Filter notifications"
        @update:model-value="filter = $event as InboxFilter"
      />
      <template #actions>
        <button
          type="button"
          class="ui-btn ui-btn-secondary"
          :disabled="!unreadCount || pending"
          @click="readAll"
        >
          Mark All Read
        </button>
      </template>
    </AppTopBar>

    <div class="ui-content narrow">
      <div class="ui-head">
        <div>
          <h1>Notifications</h1>
          <div class="sub">
            {{ unreadCount ? `${unreadCount} unread` : "All caught up" }} ·
            {{ summary.total }} matching notifications
          </div>
        </div>
      </div>
      <div class="inbox-tools">
        <label
          >Search notifications<input
            v-model="search"
            type="search"
            maxlength="200"
            placeholder="Search titles or messages"
        /></label>
        <label
          >Source<select v-model="source">
            <option value="">All sources</option>
            <option
              v-for="entry in summary.sources"
              :key="entry"
              :value="entry"
            >
              {{ entry === "host" ? "Tracking app" : entry }}
            </option>
          </select></label
        >
        <RouterLink
          to="/settings?section=notifications"
          class="ui-btn ui-btn-secondary"
          >Notification settings</RouterLink
        >
      </div>
      <p v-if="error" class="ui-state error">{{ error }}</p>
      <p v-if="loading && !items.length" class="ui-state" role="status">
        Loading…
      </p>

      <template v-if="!loading || items.length">
        <section v-if="shownReminders.length" class="plugin-reminders">
          <h2>Temporary plugin reminders</h2>
          <p class="sub">
            These plugin shortcuts are separate from your saved inbox.
          </p>
          <button
            v-for="reminder in shownReminders"
            :key="`${reminder.pluginId}:${reminder.id}`"
            type="button"
            class="ui-panel plugin-reminder"
            @click="router.push(reminder.path)"
          >
            <strong>{{ reminder.label }}</strong>
            <span>{{ reminder.description }}</span>
            <small>{{ reminder.pluginId }}</small>
          </button>
        </section>
        <div v-if="!grouped.length && !shownReminders.length" class="empty">
          <p class="empty-title">
            {{
              filter === "unread"
                ? "You're all caught up."
                : "Nothing here yet."
            }}
          </p>
          <p class="empty-sub">
            Releases, game sales, price targets, account alerts and plugin
            updates appear here. Adjust your filters or notification settings.
          </p>
        </div>

        <section v-for="[key, day] in grouped" :key="key" class="day">
          <h2 class="day-heading">{{ dayLabel(key) }}</h2>
          <div class="list">
            <section
              v-for="cluster in day"
              :key="cluster.key"
              class="notification-cluster"
            >
              <div v-if="cluster.items.length > 1" class="cluster-heading">
                <button
                  type="button"
                  class="cluster-toggle"
                  :aria-expanded="expandedGroups.has(cluster.key)"
                  @click="toggleGroup(cluster.key)"
                >
                  <strong>{{ cluster.items[0]!.title }}</strong>
                  <span>{{
                    notificationPresentation(cluster.items[0]!.kind).label
                  }}</span>
                  <span
                    >{{ cluster.items.length }} related updates ·
                    {{ cluster.items.filter((n) => !n.read).length }}
                    unread</span
                  >
                </button>
                <button
                  type="button"
                  class="ui-btn ui-btn-sm ui-btn-secondary"
                  :disabled="pending || cluster.items.every((n) => n.read)"
                  @click="groupRead(cluster.items)"
                >
                  Read these updates
                </button>
              </div>
              <div
                v-if="
                  cluster.items.length === 1 || expandedGroups.has(cluster.key)
                "
                class="list"
              >
                <article
                  v-for="n in cluster.items"
                  :key="n.id"
                  class="card"
                  :class="{
                    unread: !n.read,
                    open: openId === n.id,
                    warning: n.severity === 'warning' || n.severity === 'error',
                  }"
                >
                  <button
                    type="button"
                    class="card-row"
                    :aria-expanded="openId === n.id"
                    :aria-controls="`notice-${n.id}`"
                    @click="toggleOpen(n)"
                  >
                    <span
                      class="poster"
                      :style="
                        n.posterUrl
                          ? { backgroundImage: `url(${n.posterUrl})` }
                          : {}
                      "
                    >
                      <span v-if="!n.posterUrl" class="poster-initial">{{
                        n.title.slice(0, 1)
                      }}</span>
                    </span>
                    <span class="card-main">
                      <span class="card-title">{{ n.title }}</span>
                      <span class="card-body">{{ n.body }}</span>
                      <span class="card-time"
                        >{{ timeOnly(n.eventAt) }} ·
                        {{
                          n.source === "host" ? "Tracking app" : n.source
                        }}</span
                      >
                    </span>
                    <span
                      class="badge"
                      :class="notificationPresentation(n.kind).tone"
                      >{{ notificationPresentation(n.kind).label }}</span
                    >
                    <span
                      v-if="!n.read"
                      class="unread-dot"
                      title="Unread"
                    ></span>
                  </button>

                  <div
                    v-if="openId === n.id"
                    :id="`notice-${n.id}`"
                    class="detail"
                  >
                    <dl>
                      <div>
                        <dt>Source</dt>
                        <dd>
                          {{ n.source === "host" ? "Tracking app" : n.source }}
                        </dd>
                      </div>
                      <div>
                        <dt>What happened</dt>
                        <dd>
                          {{ notificationPresentation(n.kind).label }}:
                          {{ n.body }}
                        </dd>
                      </div>
                      <div>
                        <dt>Title</dt>
                        <dd>
                          {{ n.title }} ({{
                            TYPE_LABEL[n.mediaType] ?? "Notification"
                          }})
                        </dd>
                      </div>
                      <div>
                        <dt>
                          {{
                            n.kind === "sequel_announced"
                              ? "Noticed"
                              : "Exact time"
                          }}
                        </dt>
                        <dd>{{ exactTime(n.eventAt) }}</dd>
                      </div>
                    </dl>
                    <div class="detail-actions">
                      <button
                        v-if="notificationDestination(n)"
                        type="button"
                        class="ui-btn ui-btn-sm ui-btn-primary"
                        @click="open(n)"
                      >
                        Open {{ TYPE_LABEL[n.mediaType] ?? "item" }}
                      </button>
                      <button
                        type="button"
                        class="ui-btn ui-btn-sm ui-btn-secondary"
                        :disabled="pending"
                        @click="setRead(n, !n.read)"
                      >
                        {{ n.read ? "Mark Unread" : "Mark Read" }}
                      </button>
                      <button
                        type="button"
                        class="ui-btn ui-btn-sm ui-btn-secondary"
                        :disabled="pending"
                        @click="dismiss(n)"
                      >
                        Dismiss
                      </button>
                      <button
                        type="button"
                        class="ui-btn ui-btn-sm ui-btn-danger-soft"
                        :disabled="pending"
                        @click="remove(n)"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </article>
              </div>
            </section>
          </div>
        </section>
        <div class="inbox-pagination">
          <span class="sub"
            >{{ items.length }} of {{ summary.total }} matching
            notifications</span
          ><button
            v-if="summary.nextOffset !== null"
            type="button"
            class="ui-btn ui-btn-secondary"
            :disabled="loading || pending"
            @click="load(true)"
          >
            {{ loading ? "Loading…" : "Load more" }}
          </button>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.inbox-tools {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 22px;
}
.inbox-tools label {
  display: grid;
  gap: 6px;
  font-size: 0.78rem;
  color: var(--ui-dim);
  flex: 1;
  min-width: 160px;
}
.inbox-tools input,
.inbox-tools select {
  padding: 9px 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  color: var(--ui-text);
  background: var(--ui-surface);
  font: inherit;
  width: 100%;
  box-sizing: border-box;
}
.notification-cluster {
  display: grid;
  gap: 8px;
}
.cluster-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  background: var(--ui-surface-2);
}
.cluster-toggle {
  display: grid;
  flex: 1;
  min-width: 0;
  gap: 4px;
  text-align: left;
  color: var(--ui-text);
  border: 0;
  background: none;
  font: inherit;
  cursor: pointer;
}
.cluster-toggle span {
  font-size: 0.75rem;
  color: var(--ui-dim);
}
.inbox-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
}
.card.warning {
  border-left: 3px solid var(--ui-error);
}
.plugin-reminders {
  display: grid;
  gap: 10px;
  margin-bottom: var(--ui-space-6);
}
.plugin-reminders h2 {
  font-size: var(--ui-font-heading);
}
.plugin-reminder {
  display: grid;
  text-align: left;
  gap: 6px;
  padding: 16px;
  color: var(--ui-text);
  cursor: pointer;
}
.plugin-reminder span,
.plugin-reminder small {
  color: var(--ui-dim);
}
.empty {
  text-align: center;
  padding: 56px 16px;
}
.empty-title {
  margin: 0 0 8px;
  font-size: 1.05rem;
  font-weight: var(--ui-weight-title);
}
.empty-sub {
  margin: 0 auto;
  max-width: 460px;
  color: var(--ui-dim);
  font-size: 0.84rem;
  line-height: 1.6;
}
.day {
  margin-bottom: 26px;
}
.day-heading {
  margin: 0 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--ui-border);
  font-size: 0.74rem;
  font-weight: var(--ui-weight-title);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ui-accent-text);
}
.list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card {
  background: var(--ui-surface);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-card);
  overflow: hidden;
  transition: border-color 0.15s ease;
}
.card:hover,
.card.open {
  border-color: color-mix(in srgb, var(--ui-accent) 40%, transparent);
}
.card.unread {
  border-left: 3px solid var(--ui-accent-text);
}
.card.plain {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  text-decoration: none;
  color: inherit;
}
.card-row {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  padding: 10px 14px 10px 10px;
  background: none;
  border: none;
  text-align: left;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
}
.poster {
  width: 46px;
  height: 66px;
  flex-shrink: 0;
  border-radius: 6px;
  background: var(--ui-surface-2) center / cover;
  display: flex;
  align-items: center;
  justify-content: center;
}
.poster-initial {
  font-size: 1.2rem;
  font-weight: var(--ui-weight-title);
  color: var(--ui-faint);
}
.card-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.card-title {
  font-size: 0.92rem;
  font-weight: var(--ui-weight-title);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-body {
  font-size: 0.82rem;
  color: var(--ui-text);
}
.card-time {
  font-size: 0.72rem;
  color: var(--ui-faint);
}
.badge {
  flex-shrink: 0;
  font-size: 0.66rem;
  font-weight: var(--ui-weight-title);
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 999px;
}
.badge.amber {
  background: color-mix(in srgb, var(--ui-accent) 16%, transparent);
  color: var(--ui-accent-text);
}
.badge.green {
  background: color-mix(in srgb, var(--ui-good) 16%, transparent);
  color: var(--ui-good);
}
.badge.blue {
  background: color-mix(in srgb, var(--ui-info) 16%, transparent);
  color: var(--ui-info);
}
.badge.violet {
  background: color-mix(in srgb, var(--ui-purple) 16%, transparent);
  color: var(--ui-purple);
}
.badge.red {
  background: color-mix(in srgb, var(--ui-error) 16%, transparent);
  color: var(--ui-error);
}
.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ui-accent);
  flex-shrink: 0;
}
.detail {
  border-top: 1px solid var(--ui-border);
  padding: 14px 16px 16px 70px;
  background: var(--ui-surface-2);
}
.detail dl {
  margin: 0 0 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.detail dt {
  font-size: 0.66rem;
  font-weight: var(--ui-weight-title);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ui-faint);
}
.detail dd {
  margin: 2px 0 0;
  font-size: 0.84rem;
  color: var(--ui-text);
}
.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
@media (max-width: 720px) {
  .badge {
    display: none;
  }
  .detail {
    padding-left: 16px;
  }
}
</style>
