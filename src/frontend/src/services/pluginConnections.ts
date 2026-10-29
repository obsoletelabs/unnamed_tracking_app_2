import { dispatchPluginAction } from "./pluginUi";
import { currentUser } from "../state/auth";

// Host-side reading of a plugin connection's optional status action. The plugin
// is untrusted, so only a boolean and a short plain-text name are accepted.

export interface PluginConnectionStatus {
  connected: boolean;
  display_name?: string;
  error?: boolean;
}

const MAX_DISPLAY_NAME = 80;

export function pluginConnectionStatus(
  result: Record<string, unknown>,
): PluginConnectionStatus {
  if (result.ok === false) return { connected: false, error: true };
  const name =
    typeof result.display_name === "string" ? result.display_name.trim() : "";
  return {
    connected: result.connected === true,
    ...(name ? { display_name: name.slice(0, MAX_DISPLAY_NAME) } : {}),
  };
}

const statusCache = new Map<
  string,
  { value: PluginConnectionStatus; expiresAt: number }
>();
const statusRequests = new Map<string, Promise<PluginConnectionStatus>>();

export function clearPluginConnectionStatuses(): void {
  statusCache.clear();
  statusRequests.clear();
}

export async function readPluginConnectionStatus(
  pluginId: string,
  actionId: string,
  force = false,
): Promise<PluginConnectionStatus> {
  const key = JSON.stringify([currentUser.value?.id, pluginId, actionId]);
  const cached = statusCache.get(key);
  if (!force && cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = statusRequests.get(key);
  if (!force && pending) return pending;
  const request = dispatchPluginAction(pluginId, actionId)
    .then((result) => {
      const value = pluginConnectionStatus(result);
      if (statusRequests.get(key) === request) {
        statusCache.set(key, { value, expiresAt: Date.now() + 15_000 });
        while (statusCache.size > 128)
          statusCache.delete(statusCache.keys().next().value!);
      }
      return value;
    })
    .finally(() => {
      if (statusRequests.get(key) === request) statusRequests.delete(key);
    });
  statusRequests.set(key, request);
  return request;
}
