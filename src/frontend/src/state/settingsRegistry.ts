import { computed } from "vue";
import { currentUser } from "./auth";
import {
  pluginNavigation,
  pluginSettingsSections,
  type PluginNavigationContribution,
  type PluginSettingsContribution,
} from "./pluginExtensions";
import type {
  SettingsGroup,
  SettingsSection,
} from "../components/settings/SettingsNav.vue";
import type { SettingsArea } from "../utils/pluginPlacement";

// One registry feeds the Settings sidebar, landing page, in-page search and the
// command palette, so every surface offers exactly the same accessible targets
// in both Modern and Classic.

export const CORE_SETTINGS_SECTION_IDS: ReadonlySet<string> = new Set([
  "admin",
  "limits",
  "dev-tools",
  "metadata",
  "connections",
  "notifications",
  "admin-notifications",
  "calendar",
  "shortcuts",
  "profile",
  "interface",
  "appearance",
  "game-page",
  "app-installation",
  "branding",
  "api-keys",
  "connected-apps",
  "integration-policy",
  "sessions",
  "admin-sessions",
  "calendar-notifications",
  "upload",
  "library",
  "media-prefs",
  "media-trash",
  "scan",
  "sources",
  "media-refresh",
  "export",
  "oidc",
  "password-policy",
  "server-integrations",
  "app-settings",
  "users",
  "plugins",
  "themes",
  "stats",
  "tasks",
  "background-tasks",
  "logs",
]);

export const SETTINGS_AREA_NAMES: Record<SettingsArea, string> = {
  preferences: "Preferences",
  account: "Account",
  administration: "Administration",
};

export interface SettingsTab {
  id: string;
  label: string;
}

export const LIBRARY_SETTINGS_TABS: SettingsTab[] = [
  { id: "manage", label: "Library" },
  { id: "preferences", label: "Preferences" },
  { id: "duplicates", label: "Duplicates" },
  { id: "versions", label: "Versions" },
  { id: "names", label: "Names" },
  { id: "trash", label: "Trash" },
];

export const EXPORT_SETTINGS_TABS: SettingsTab[] = [
  { id: "export", label: "Export" },
  { id: "import", label: "Import" },
  { id: "restore", label: "Restore" },
  { id: "backup", label: "Backups" },
];

export const METADATA_SETTINGS_TABS: SettingsTab[] = [
  { id: "sources", label: "Sources & API keys" },
  { id: "scan", label: "Scan & providers" },
  { id: "refresh", label: "Refresh media" },
];

// Account → Notifications and Administration → Notifications are split by
// context so important settings are reachable without long scrolling.
export const NOTIFICATION_SETTINGS_TABS: SettingsTab[] = [
  { id: "providers", label: "Providers" },
  { id: "types", label: "Notification types" },
  { id: "timing", label: "Timing & quiet hours" },
  { id: "history", label: "History" },
];

export const ADMIN_NOTIFICATION_SETTINGS_TABS: SettingsTab[] = [
  { id: "email", label: "Email (SMTP)" },
  { id: "push", label: "Browser push" },
  { id: "plugins", label: "Plugin providers" },
  { id: "retention", label: "Inbox retention" },
];

// Sections that used to be their own entries and now live inside a
// merged page as a tab, so old links (command palette, bookmarks) still
// land in the right place.
export const SETTINGS_SECTION_ALIASES: Record<
  string,
  { section: string; tab?: string }
> = {
  interface: { section: "appearance" },
  "calendar-notifications": { section: "notifications" },
  "media-prefs": { section: "library", tab: "preferences" },
  "media-trash": { section: "library", tab: "trash" },
  sources: { section: "metadata", tab: "sources" },
  scan: { section: "metadata", tab: "scan" },
  "media-refresh": { section: "metadata", tab: "refresh" },
};

export function resolveSettingsSection(
  id: string | undefined,
  legacyAdminTab?: string,
): { section: string; tab?: string } {
  const raw = id || "";
  if (raw === "admin") {
    const sections: Record<string, string> = {
      users: "users",
      sso: "oidc",
      integrations: "server-integrations",
      app: "app-settings",
      notifications: "admin-notifications",
      limits: "limits",
      "dev-tools": "dev-tools",
    };
    return { section: sections[legacyAdminTab ?? "users"] ?? "users" };
  }
  return SETTINGS_SECTION_ALIASES[raw] ?? { section: raw };
}

export function visiblePluginSettingsFor(
  sections: readonly PluginSettingsContribution[],
  isAdmin: boolean,
): PluginSettingsContribution[] {
  const seen = new Set<string>();
  return sections.filter((item) => {
    if (item.adminOnly && !isAdmin) return false;
    if (
      CORE_SETTINGS_SECTION_IDS.has(item.contributionId) ||
      seen.has(item.contributionId)
    )
      return false;
    seen.add(item.contributionId);
    return true;
  });
}

export function visiblePluginSettingsNavigationFor(
  navigation: readonly PluginNavigationContribution[],
  settings: readonly PluginSettingsContribution[],
  isAdmin: boolean,
): PluginNavigationContribution[] {
  const seen = new Set(settings.map((item) => item.contributionId));
  return navigation.filter((item) => {
    if (item.location !== "settings.sidebar" || !item.pageId) return false;
    if (item.adminOnly && !isAdmin) return false;
    if (
      CORE_SETTINGS_SECTION_IDS.has(item.contributionId) ||
      seen.has(item.contributionId)
    )
      return false;
    seen.add(item.contributionId);
    return true;
  });
}

export function buildSettingsGroups(
  isAdmin: boolean,
  pluginSettings: readonly PluginSettingsContribution[],
  pluginSettingsNavigation: readonly PluginNavigationContribution[],
): SettingsGroup[] {
  const result: SettingsGroup[] = [
    {
      label: "Account",
      area: "account",
      sections: [
        { id: "profile", label: "Profile" },
        { id: "connections", label: "Connections" },
        { id: "background-tasks", label: "Background tasks" },
        { id: "notifications", label: "Notifications" },
        { id: "api-keys", label: "API Keys" },
        { id: "connected-apps", label: "Connected apps" },
        { id: "sessions", label: "Sessions" },
      ],
    },
    {
      label: "Preferences",
      area: "preferences",
      sections: [
        { id: "appearance", label: "Appearance & interface" },
        { id: "app-installation", label: "App installation" },
        { id: "game-page", label: "Game page" },
        { id: "calendar", label: "Calendar" },
        { id: "shortcuts", label: "Keyboard Shortcuts" },
      ],
    },
    {
      label: "Library",
      area: "preferences",
      sections: [
        { id: "library", label: "Library" },
        { id: "metadata", label: "Metadata" },
        { id: "export", label: "Export / Import" },
      ],
    },
  ];
  if (isAdmin)
    result.push(
      {
        label: "Users & access",
        area: "administration",
        sections: [
          { id: "users", label: "Users" },
          { id: "admin-sessions", label: "Session Manager" },
          { id: "oidc", label: "Single sign-on" },
          { id: "password-policy", label: "Password policy" },
          { id: "integration-policy", label: "Integration access" },
        ],
      },
      {
        label: "Application & extensions",
        area: "administration",
        sections: [
          { id: "app-settings", label: "Application" },
          { id: "branding", label: "App branding" },
          { id: "plugins", label: "Plugins" },
          { id: "themes", label: "Themes" },
        ],
      },
      {
        label: "Operations",
        area: "administration",
        sections: [
          { id: "admin-notifications", label: "Notifications" },
          { id: "server-integrations", label: "Server integrations" },
          { id: "limits", label: "Upload limits" },
          { id: "tasks", label: "Background tasks" },
          { id: "stats", label: "Storage & usage" },
          { id: "dev-tools", label: "Developer tools" },
        ],
      },
    );
  else
    result.push({
      label: "Information",
      area: "preferences",
      sections: [{ id: "stats", label: "Storage & usage" }],
    });
  for (const item of [...pluginSettings, ...pluginSettingsNavigation]) {
    let group = result.find(
      (candidate) =>
        candidate.area === item.area && candidate.label === item.group,
    );
    if (!group) {
      group = { label: item.group, area: item.area, sections: [] };
      result.push(group);
    }
    group.sections.push({
      id: item.contributionId,
      label: item.label,
      folders: item.folders,
    });
  }
  return result;
}

export const visiblePluginSettings = computed(() =>
  visiblePluginSettingsFor(
    pluginSettingsSections.value,
    !!currentUser.value?.is_admin,
  ),
);
export const visiblePluginSettingsNavigation = computed(() =>
  visiblePluginSettingsNavigationFor(
    pluginNavigation.value,
    visiblePluginSettings.value,
    !!currentUser.value?.is_admin,
  ),
);
export const settingsGroups = computed(() =>
  buildSettingsGroups(
    !!currentUser.value?.is_admin,
    visiblePluginSettings.value,
    visiblePluginSettingsNavigation.value,
  ),
);

// Search vocabulary is deliberately static: section names plus hand-written
// keywords. Field values, plugin configuration and secrets are never indexed.
const SECTION_KEYWORDS: Record<string, string[]> = {
  profile: ["account", "name", "username", "email", "password", "avatar"],
  connections: ["linked accounts", "connected services", "sign in"],
  "background-tasks": ["jobs", "queue", "progress"],
  notifications: ["alerts", "push", "reminders", "email"],
  "api-keys": ["tokens", "access keys", "developer"],
  "connected-apps": ["oauth", "authorized apps", "clients"],
  sessions: ["devices", "sign out", "logins"],
  appearance: ["theme", "dark mode", "light mode", "colors", "palette"],
  "app-installation": ["pwa", "install", "home screen"],
  "game-page": ["game details", "layout"],
  calendar: ["releases", "schedule", "ical", "feed"],
  shortcuts: ["keyboard", "hotkeys", "keys"],
  library: ["manage", "media"],
  metadata: ["providers", "sources", "lookup"],
  export: ["backup", "import", "restore", "csv", "json"],
  stats: ["storage", "disk", "usage"],
  users: ["accounts", "invites", "roles"],
  "admin-sessions": ["sessions", "devices", "sign out"],
  oidc: ["sso", "openid", "oauth", "login provider"],
  "password-policy": ["password rules", "length", "complexity"],
  "integration-policy": ["integrations", "permissions"],
  "app-settings": ["application", "tls", "https", "certificate", "proxy"],
  branding: ["logo", "app name", "favicon"],
  plugins: ["extensions", "plugin manager", "install"],
  themes: ["custom themes", "css"],
  "admin-notifications": ["smtp", "email server", "server notifications"],
  "server-integrations": ["server", "integrations"],
  limits: ["upload limits", "file size"],
  tasks: ["server background tasks", "scheduler", "jobs"],
  "dev-tools": ["developer", "debug"],
};

// Tabs inside merged sections that are worth reaching directly. Each one is
// searchable only while its containing section is available.
const TAB_ENTRIES: {
  section: string;
  tab: string;
  label: string;
  keywords: string[];
}[] = [
  {
    section: "library",
    tab: "preferences",
    label: "Library preferences",
    keywords: ["media preferences"],
  },
  {
    section: "library",
    tab: "duplicates",
    label: "Duplicates",
    keywords: ["merge", "duplicate media"],
  },
  {
    section: "library",
    tab: "trash",
    label: "Media trash",
    keywords: ["trash", "deleted", "restore"],
  },
  {
    section: "export",
    tab: "export",
    label: "Export JSON or CSV",
    keywords: ["download", "json", "csv", "spreadsheet"],
  },
  {
    section: "export",
    tab: "import",
    label: "Import from another site",
    keywords: [
      "myanimelist",
      "mal",
      "letterboxd",
      "imdb",
      "yamtrack",
      "anilist",
    ],
  },
  {
    section: "export",
    tab: "restore",
    label: "Restore from export",
    keywords: ["restore", "import games", "export file"],
  },
  {
    section: "export",
    tab: "backup",
    label: "Automatic backups",
    keywords: ["backup", "snapshot", "disaster recovery"],
  },
  {
    section: "metadata",
    tab: "sources",
    label: "Sources & API keys",
    keywords: ["metadata api keys", "provider keys"],
  },
  {
    section: "metadata",
    tab: "scan",
    label: "Scan & providers",
    keywords: ["scan", "provider order"],
  },
  {
    section: "metadata",
    tab: "refresh",
    label: "Refresh media",
    keywords: ["refresh", "update metadata"],
  },
  {
    section: "notifications",
    tab: "providers",
    label: "Notification providers",
    keywords: ["destinations", "email addresses", "webhooks", "link urls"],
  },
  {
    section: "notifications",
    tab: "types",
    label: "Notification types",
    keywords: ["routing", "what to send", "alerts"],
  },
  {
    section: "notifications",
    tab: "timing",
    label: "Notification timing",
    keywords: ["quiet hours", "digest", "batching", "schedule"],
  },
  {
    section: "notifications",
    tab: "history",
    label: "Notification history",
    keywords: ["delivery history", "inbox history", "retention"],
  },
  {
    section: "admin-notifications",
    tab: "email",
    label: "Email delivery (SMTP)",
    keywords: ["smtp", "email server", "mail"],
  },
  {
    section: "admin-notifications",
    tab: "push",
    label: "Browser push",
    keywords: ["web push", "vapid", "push notifications"],
  },
  {
    section: "admin-notifications",
    tab: "plugins",
    label: "Plugin notification providers",
    keywords: ["notification plugins", "provider credentials"],
  },
  {
    section: "admin-notifications",
    tab: "retention",
    label: "Inbox retention",
    keywords: ["retention policy", "notification retention"],
  },
];

export interface SettingsSearchEntry {
  key: string;
  section: string;
  tab?: string;
  label: string;
  context: string;
  area: SettingsArea;
  keywords: string[];
}

export function buildSettingsSearchIndex(
  groups: readonly SettingsGroup[],
): SettingsSearchEntry[] {
  const entries: SettingsSearchEntry[] = [];
  const located = new Map<
    string,
    { group: SettingsGroup; section: SettingsSection }
  >();
  for (const group of groups)
    for (const section of group.sections) {
      if (located.has(section.id)) continue;
      located.set(section.id, { group, section });
      entries.push({
        key: `section:${section.id}`,
        section: section.id,
        label: section.label,
        context:
          group.label === SETTINGS_AREA_NAMES[group.area]
            ? group.label
            : `${SETTINGS_AREA_NAMES[group.area]} · ${group.label}`,
        area: group.area,
        keywords: SECTION_KEYWORDS[section.id] ?? [],
      });
    }
  for (const tab of TAB_ENTRIES) {
    const parent = located.get(tab.section);
    if (!parent) continue;
    entries.push({
      key: `tab:${tab.section}:${tab.tab}`,
      section: tab.section,
      tab: tab.tab,
      label: tab.label,
      context: `${SETTINGS_AREA_NAMES[parent.group.area]} · ${parent.section.label}`,
      area: parent.group.area,
      keywords: tab.keywords,
    });
  }
  return entries;
}

export const settingsSearchIndex = computed(() =>
  buildSettingsSearchIndex(settingsGroups.value),
);

export function searchSettings(
  index: readonly SettingsSearchEntry[],
  query: string,
  limit = 8,
): SettingsSearchEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const terms = q.split(/\s+/);
  const ranked: { entry: SettingsSearchEntry; rank: number; order: number }[] =
    [];
  index.forEach((entry, order) => {
    const label = entry.label.toLowerCase();
    const keywords = entry.keywords.map((keyword) => keyword.toLowerCase());
    const matches = (term: string) =>
      label.includes(term) ||
      keywords.some((keyword) => keyword.includes(term));
    if (!terms.every(matches)) return;
    const rank = label.startsWith(q)
      ? 0
      : label.includes(q)
        ? 1
        : keywords.some((keyword) => keyword.startsWith(q))
          ? 2
          : 3;
    ranked.push({ entry, rank, order });
  });
  return ranked
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .slice(0, limit)
    .map((item) => item.entry);
}

export function settingsSearchLocation(entry: SettingsSearchEntry): {
  path: string;
  query: Record<string, string>;
} {
  return {
    path: "/settings",
    query: { section: entry.section, ...(entry.tab ? { tab: entry.tab } : {}) },
  };
}
