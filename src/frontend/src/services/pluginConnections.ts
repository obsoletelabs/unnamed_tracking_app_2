// Plugin connections as the host reports them. The host asks each plugin for its
// account status and runs the sync itself; the browser only renders this state.

import { providerRequestError } from "./apiError";

export interface PluginConnectionSync {
  available: boolean;
  unavailable_reason: string;
  running: boolean;
  paused: boolean;
  // idle | partial | completed | failed | cancelled | disabled | paused
  status: string;
  last_run_at: number | null;
  last_summary: string;
}

export interface PluginConnectionState {
  plugin_id: string;
  connection_id: string;
  connected: boolean;
  display_name: string | null;
  error: boolean;
  // null when the connection declares no sync
  sync: PluginConnectionSync | null;
}

export function pluginConnectionKey(
  pluginId: string,
  connectionId: string,
): string {
  return `${pluginId}:${connectionId}`;
}

const connectionsUrl = "/api/connections/plugins";

export async function fetchPluginConnections(): Promise<
  PluginConnectionState[]
> {
  const response = await fetch(connectionsUrl, { credentials: "include" });
  if (!response.ok) throw await providerRequestError(response);
  const body = (await response.json()) as {
    connections: PluginConnectionState[];
  };
  return body.connections;
}

export async function startPluginConnectionSync(
  pluginId: string,
  connectionId: string,
): Promise<PluginConnectionSync> {
  const response = await fetch(
    `${connectionsUrl}/${encodeURIComponent(pluginId)}/${encodeURIComponent(connectionId)}/sync`,
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) throw await providerRequestError(response);
  return ((await response.json()) as { sync: PluginConnectionSync }).sync;
}
