import { announceProviderChange } from "./providerEvents";
import { failedRequest } from "./apiError";

export interface CurrentUser {
  id: string;
  // Immutable sign-in identifier; never replaced by the display name.
  username: string;
  // Optional visible name chosen by the user (null/absent = use the username).
  display_name?: string | null;
  email: string;
  is_admin: boolean;
  // Legacy response slot is always null; the credential is write-only.
  steamgriddb_api_key: null;
  steamgriddb_configured?: boolean;
  profile_picture_version?: string | null;
}

/** Name shown to people: the chosen display name, else the sign-in username. */
export function accountDisplayName(
  user: Pick<CurrentUser, "username" | "display_name"> | null | undefined,
): string {
  if (!user) return "";
  return user.display_name?.trim() || user.username;
}

/** Up to two user-perceived characters (not UTF-16 halves) for avatar fallbacks. */
export function accountInitials(
  user: Pick<CurrentUser, "username" | "display_name"> | null | undefined,
): string {
  const name = accountDisplayName(user);
  if (!name) return "?";
  const characters = Array.from(
    new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(name),
    (part) => part.segment,
  );
  return characters.slice(0, 2).join("").toUpperCase();
}

export interface ApiKeySummary {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  created_at: number;
}

export interface CreatedApiKey {
  api_key: string;
  key_prefix: string;
  name: string;
  scopes: string[];
  warning: string;
}

export async function login(
  usernameOrEmail: string,
  password: string,
): Promise<void> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return;
  }

  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username_or_email: usernameOrEmail, password }),
  });

  if (!response.ok) {
    if (response.status === 401) {
      throw new Error("Invalid username/email or password.");
    }
    const message = await response.text();
    throw new Error(
      `Login failed: ${response.status} ${response.statusText} ${message}`,
    );
  }
}

export async function logout(): Promise<void> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return;
  }
  const response = await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) throw new Error("Could not sign out. Please try again.");
  // Server session revocation is authoritative even if the browser is offline.
  await import("./pwa")
    .then((pwa) => pwa.unsubscribeBrowserPush())
    .catch(() => {});
}

export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") {
    return {
      id: "mock",
      username: "MockUser",
      display_name: null,
      email: "mock@example.com",
      is_admin: true,
      steamgriddb_api_key: null,
    };
  }

  const response = await fetch("/api/auth/me", { credentials: "include" });
  if (response.status === 401) return null;
  if (!response.ok) {
    throw new Error(
      `Failed to fetch current user: ${response.status} ${response.statusText}`,
    );
  }
  return await response.json();
}

export async function updateProfile(
  payload: UpdateProfilePayload,
): Promise<CurrentUser> {
  const response = await fetch("/api/auth/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      username: payload.username,
      display_name: payload.displayName,
      email: payload.email,
      current_password: payload.currentPassword,
      new_password: payload.newPassword,
      steamgriddb_api_key: payload.steamgriddbApiKey,
    }),
  });

  if (response.status === 401) {
    throw new Error("Current password is incorrect.");
  }
  if (!response.ok) {
    throw await failedRequest(response);
  }

  // a changed SteamGridDB key changes which artwork sources can run
  if (payload.steamgriddbApiKey !== undefined) announceProviderChange();
  return await response.json();
}

export async function fetchApiKeys(): Promise<ApiKeySummary[]> {
  const response = await fetch("/api/auth/api-keys", {
    credentials: "include",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to load API keys: ${response.status} ${response.statusText} ${message}`,
    );
  }
  return await response.json();
}

export async function createApiKey(
  name: string,
  scopes: string[] = [],
): Promise<CreatedApiKey> {
  const response = await fetch("/api/auth/api-keys", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ name, scopes }),
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to create API key: ${response.status} ${response.statusText} ${message}`,
    );
  }
  return await response.json();
}

export async function revokeApiKey(keyId: string): Promise<void> {
  const response = await fetch(`/api/auth/api-keys/${keyId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(
      `Failed to revoke API key: ${response.status} ${response.statusText} ${message}`,
    );
  }
}

export interface UpdateProfilePayload {
  username?: string;
  // Omit to keep the current value; a blank string clears it.
  displayName?: string;
  email?: string;
  currentPassword?: string;
  newPassword?: string;
  steamgriddbApiKey?: string;
}

export async function uploadProfilePicture(
  userId: string,
  file: File,
): Promise<string | null> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`/api/user/${userId}/profile-picture`, {
    method: "PUT",
    credentials: "include",
    body: formData,
  });

  if (!response.ok) {
    // Show the validation message returned by the upload endpoint.
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Failed to upload profile picture (${response.status}).`,
    );
  }
  const result: { profile_picture_version?: string | null } =
    await response.json();
  return result.profile_picture_version ?? null;
}

export async function removeProfilePicture(
  userId: string,
): Promise<string | null> {
  const response = await fetch(
    `/api/user/${encodeURIComponent(userId)}/profile-picture`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Failed to remove profile picture (${response.status}).`,
    );
  }
  const result: { profile_picture_version?: string | null } =
    await response.json();
  return result.profile_picture_version ?? null;
}

export function profilePictureUrl(userId: string, version: string): string {
  return `/api/user/${encodeURIComponent(userId)}/profile-picture?v=${encodeURIComponent(version)}`;
}
