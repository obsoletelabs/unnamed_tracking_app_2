import { failedRequest } from "./apiError";

export type SessionState = "active" | "expired" | "revoked";

export interface BrowserSession {
  id: string;
  user_id: string;
  username: string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: number;
  last_seen_at: number;
  expires_at: number;
  revoked_at: number | null;
  state: SessionState;
  is_current: boolean;
}

async function sessionRequest<T>(path: string, method = "GET"): Promise<T> {
  const response = await fetch(`/api/sessions/${path}`, {
    method,
    credentials: "include",
  });
  if (!response.ok) throw await failedRequest(response);
  return response.json();
}

export function fetchSessions(admin = false): Promise<BrowserSession[]> {
  return sessionRequest(`${admin ? "admin" : "me"}?enriched=false`);
}

export function revokeSession(
  id: string,
  admin = false,
): Promise<{ status: string }> {
  return sessionRequest(
    `${admin ? "admin" : "me"}/${encodeURIComponent(id)}`,
    "DELETE",
  );
}

export function revokeSessions(
  admin = false,
  userId?: string,
): Promise<{ revoked: number }> {
  const path =
    admin && userId
      ? `admin/user/${encodeURIComponent(userId)}`
      : `${admin ? "admin" : "me"}/all`;
  return sessionRequest(path, "DELETE");
}

export function sessionDevice(agent: string | null): string {
  if (!agent) return "Browser unavailable";
  const browser = /Edg(?:e|A|iOS)?\/([\d.]+)/.exec(agent)
    ? "Edge"
    : /(?:Firefox|FxiOS)\//.test(agent)
      ? "Firefox"
      : /(?:Chrome|CriOS)\//.test(agent)
        ? "Chrome"
        : /Safari\//.test(agent)
          ? "Safari"
          : "Other browser";
  const system = /Android/.test(agent)
    ? "Android"
    : /iPhone|iPad|iPod/.test(agent)
      ? "iOS"
      : /Windows/.test(agent)
        ? "Windows"
        : /Macintosh|Mac OS X/.test(agent)
          ? "macOS"
          : /Linux/.test(agent)
            ? "Linux"
            : "";
  return system ? `${browser} on ${system}` : browser;
}
