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

export interface PluginSyncProgress {
  complete: boolean;
  message?: string;
}

const MAX_SYNC_MESSAGE = 160;
// Each call is one bounded plugin step. The cap stops a plugin that never
// reports completion from being called forever; a later run resumes.
export const MAX_SYNC_STEPS = 5000;

/** Read one sync step result. Failures and malformed results throw. */
export function pluginSyncProgress(
  result: Record<string, unknown>,
): PluginSyncProgress {
  if (result.ok === false) {
    const error =
      typeof result.error === "string" ? result.error.slice(0, 300) : "";
    throw new Error(error || "The sync could not complete. Try again.");
  }
  if (typeof result.complete !== "boolean")
    throw new Error("The plugin returned an unexpected sync result.");
  const message =
    typeof result.message === "string"
      ? result.message.trim().slice(0, MAX_SYNC_MESSAGE)
      : "";
  return { complete: result.complete, ...(message ? { message } : {}) };
}

/**
 * Run a plugin's sync action until it reports completion. `step` performs one
 * bounded call; `onProgress` receives each distinct message as it changes.
 */
export async function runPluginSync(
  step: () => Promise<Record<string, unknown>>,
  onProgress: (message: string) => void = () => {},
  maxSteps = MAX_SYNC_STEPS,
): Promise<string | undefined> {
  let last: string | undefined;
  for (let count = 0; count < maxSteps; count++) {
    const progress = pluginSyncProgress(await step());
    if (progress.message && progress.message !== last) {
      last = progress.message;
      onProgress(progress.message);
    }
    if (progress.complete) return progress.message;
  }
  throw new Error("The sync did not finish. Run it again to resume.");
}
