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
