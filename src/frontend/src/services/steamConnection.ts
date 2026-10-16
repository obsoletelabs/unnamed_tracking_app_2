// Steam's EAuthSessionGuardType values for codes a person types in.
export const STEAM_GUARD_EMAIL_CODE = 2;
export const STEAM_GUARD_DEVICE_CODE = 3;
export type SteamGuardCodeType =
  typeof STEAM_GUARD_EMAIL_CODE | typeof STEAM_GUARD_DEVICE_CODE;

export interface SteamLoginAttempt {
  attempt_id: string;
  expires_at: number;
  interval: number;
  challenge_url: string | null;
  confirmation_types: number[];
  phase: "starting" | "waiting" | "checking";
}

export interface SteamConnection {
  connected: boolean;
  method: "account" | "api_key" | null;
  display_name: string | null;
  expires_at: number | null;
  pending: SteamLoginAttempt | null;
}

export class SteamConnectionError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request(
  path = "",
  method = "GET",
  body?: object,
): Promise<SteamConnection> {
  const response = await fetch(`/api/settings/steam${path}`, {
    method,
    credentials: "include",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    const detail =
      data &&
      typeof data === "object" &&
      "detail" in data &&
      typeof data.detail === "string"
        ? data.detail
        : "Steam connection request failed. Try again.";
    throw new SteamConnectionError(detail, response.status);
  }
  return response.json();
}

export const fetchSteamConnection = () => request();
export const beginSteamQr = () => request("/login/qr", "POST");
export const beginSteamAccount = (account_name: string, password: string) =>
  request("/login/account", "POST", { account_name, password });
export const pollSteamLogin = (attempt_id: string) =>
  request("/login/poll", "POST", { attempt_id });
export const submitSteamGuard = (
  attempt_id: string,
  code: string,
  code_type: SteamGuardCodeType,
) => request("/login/guard", "POST", { attempt_id, code, code_type });
export const cancelSteamLogin = (attempt_id: string) =>
  request("/login/cancel", "POST", { attempt_id });
export const disconnectSteam = () => request("", "DELETE");
