import { ref } from "vue";
import { currentUser } from "./auth";
import { pluginRecordPathAllowed } from "./pluginSearch";

export interface PluginReminder {
  id: string;
  label: string;
  description: string;
  path: string;
}
type Provider = () => Promise<PluginReminder[]>;
const providers = new Map<string, Provider>();
const requests = new Map<
  string,
  {
    provider: Provider;
    account?: string;
    pending?: Promise<PluginReminder[]>;
    value?: PluginReminder[];
    expiresAt: number;
  }
>();

async function providerReminders(
  pluginId: string,
  provider: Provider,
): Promise<PluginReminder[]> {
  const account = currentUser.value?.id;
  const cached = requests.get(pluginId);
  if (cached?.provider === provider && cached.account === account) {
    if (cached.pending) return cached.pending;
    if (cached.value && cached.expiresAt > Date.now()) return cached.value;
  }
  const entry: {
    provider: Provider;
    account?: string;
    pending?: Promise<PluginReminder[]>;
    value?: PluginReminder[];
    expiresAt: number;
  } = { provider, account, expiresAt: 0 };
  const pending = provider()
    .then((value) => {
      if (
        requests.get(pluginId) === entry &&
        account === currentUser.value?.id
      ) {
        entry.value = value;
        entry.expiresAt = Date.now() + 15_000;
      }
      return value;
    })
    .finally(() => {
      entry.pending = undefined;
    });
  entry.pending = pending;
  requests.set(pluginId, entry);
  return pending;
}
export const pluginReminderRevision = ref(0);

export function registerPluginReminders(
  pluginId: string,
  provider: Provider,
): () => void {
  requests.delete(pluginId);
  providers.set(pluginId, provider);
  pluginReminderRevision.value++;
  return () => {
    if (providers.get(pluginId) === provider) {
      providers.delete(pluginId);
      requests.delete(pluginId);
      pluginReminderRevision.value++;
    }
  };
}

export async function readPluginReminders(): Promise<
  Array<PluginReminder & { pluginId: string }>
> {
  const account = currentUser.value?.id;
  const results = await Promise.all(
    [...providers].map(async ([pluginId, provider]) => {
      try {
        const items = await providerReminders(pluginId, provider);
        if (
          providers.get(pluginId) !== provider ||
          account !== currentUser.value?.id
        )
          return [];
        return items
          .filter(
            (item) =>
              typeof item?.id === "string" &&
              typeof item.label === "string" &&
              typeof item.description === "string" &&
              pluginRecordPathAllowed(pluginId, item.path),
          )
          .slice(0, 50)
          .map((item) => ({ ...item, pluginId }));
      } catch {
        return [];
      }
    }),
  );
  return results.flat().slice(0, 200);
}
