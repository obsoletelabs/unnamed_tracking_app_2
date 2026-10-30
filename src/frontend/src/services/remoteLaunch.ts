import type { IntegrationGrant } from "./delegation";
import { failedRequest } from "./apiError";

export interface LaunchDevice {
  id: string;
  name: string;
  online: boolean;
  last_seen_at: number;
}
export type LaunchState =
  "pending" | "dispatched" | "accepted" | "failed" | "expired" | "unknown";
export interface RemoteLaunchRequest {
  id: string;
  device_id: string;
  game_id: string;
  state: LaunchState;
  failure_code: string | null;
  created_at: number;
  expires_at: number;
}
export interface LaunchCommand {
  device_id: string;
  game_id: string;
  request_key: string;
}
const base = "/api/playnite/launch/v1";
async function request<T>(
  path: string,
  signal: AbortSignal,
  init?: RequestInit,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(abort, 10000);
  try {
    const response = await fetch(base + path, {
      ...init,
      signal: controller.signal,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
    if (!response.ok) throw await failedRequest(response);
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", abort);
  }
}
export const fetchLaunchDevices = (signal: AbortSignal) =>
  request<LaunchDevice[]>("/devices", signal);
export const queueRemoteLaunch = (
  command: LaunchCommand,
  signal: AbortSignal,
) =>
  request<RemoteLaunchRequest>("/requests", signal, {
    method: "POST",
    body: JSON.stringify(command),
  });
export const fetchRemoteLaunch = (id: string, signal: AbortSignal) =>
  request<RemoteLaunchRequest>("/requests/" + encodeURIComponent(id), signal);

const failures: Record<string, string> = {
  game_not_found: "This game is not in that computer’s Playnite library.",
  game_unavailable:
    "The game changed or became unavailable before it could launch.",
  not_installed: "The game is not installed on that computer.",
  already_running: "The game is already running on that computer.",
  declined: "The launch was declined or cancelled on that computer.",
  launch_failed:
    "Playnite could not start the game. Check the computer for details.",
};
export function remoteLaunchMessage(
  result: RemoteLaunchRequest,
  name: string,
): string {
  switch (result.state) {
    case "pending":
      return "Waiting for " + name + " to receive the launch request…";
    case "dispatched":
      return name + " received the request. Waiting for Playnite…";
    case "accepted":
      return (
        "Playnite on " +
        name +
        " accepted the launch. Check that computer for the game or any launcher prompts."
      );
    case "failed":
      return (
        failures[result.failure_code ?? ""] ??
        "The game could not be launched on " + name + "."
      );
    case "expired":
      return (
        "The request expired without being launched. Check that " +
        name +
        " is online and receiving requests."
      );
    case "unknown":
      return (
        "The launch result from " +
        name +
        " could not be confirmed. Check that computer before launching again."
      );
  }
}

export const MINIMUM_REMOTE_LAUNCH_VERSION = "0.3.0";
export interface PlayniteConnection {
  id: string;
  name: string;
  version: string | null;
  support: "supported" | "outdated" | "unreported" | "unknown";
}
export function playniteConnections(
  grants: IntegrationGrant[],
): PlayniteConnection[] {
  return grants
    .filter(
      (grant) =>
        grant.kind === "desktop" &&
        grant.origin === "desktop:playnite" &&
        grant.enabled &&
        !grant.revoked_at &&
        (grant.expires_at === null || grant.expires_at > Date.now() / 1000),
    )
    .map((grant) => {
      const reported = grant.external_account?.playnite_extension_version;
      const version =
        reported && /^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$/.test(reported)
          ? reported
          : null;
      const parts = version?.split(".").map(Number);
      const supported = !!parts && (parts[0]! > 0 || parts[1]! >= 3);
      return {
        id: grant.id,
        name: grant.external_account?.username || "Playnite computer",
        version,
        support: version
          ? supported
            ? "supported"
            : "outdated"
          : grant.external_account?.playnite_version_checked_at
            ? "unreported"
            : "unknown",
      };
    });
}
