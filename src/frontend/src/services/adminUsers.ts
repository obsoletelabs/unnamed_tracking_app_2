import { failedRequest } from "./apiError";

export interface Confirmation {
  adminPassword: string;
  confirmUsername: string;
}

export interface Invitation {
  id: string;
  email: string;
  status: "pending" | "expired" | "revoked" | "accepted";
  delivery_status: string | null;
  created_at: number;
  expires_at: number;
}

export interface EmailDestination {
  id: string;
  address: string;
  is_account_email: boolean;
  verified: boolean;
  recovery_allowed: boolean;
  enabled: boolean;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  outcome: "success" | "denied" | "refused";
  detail: string;
  occurred_at: number;
}

// Admin actions answer with specific, reviewed sentences (wrong password,
// no verified recovery address); show them instead of the generic mapping.
async function adminError(response: Response): Promise<Error> {
  if (response.status === 401 || response.status >= 500)
    return failedRequest(response);
  const body = await response.text();
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === "string") return new Error(detail);
  } catch {
    // fall through to the generic message
  }
  return new Error(`The request failed (${response.status}).`);
}

async function send<T>(
  path: string,
  method: "GET" | "POST" | "DELETE",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "include",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await adminError(response);
  return (await response.json()) as T;
}

const proof = (c: Confirmation) => ({
  admin_password: c.adminPassword,
  confirm_username: c.confirmUsername,
});

export function listInvitations(): Promise<Invitation[]> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return Promise.resolve([]);
  return send("/api/auth/user-invitations", "GET");
}

export const resendInvitation = (id: string) =>
  send<{ status: string; id: string }>(
    `/api/auth/user-invitations/${id}/resend`,
    "POST",
  );

export const withdrawInvitation = (id: string) =>
  send<{ status: string }>(`/api/auth/user-invitations/${id}`, "DELETE");

export const changeUserEmail = (id: string, email: string, c: Confirmation) =>
  send<{ email: string }>(`/api/auth/users/${id}/email`, "POST", {
    email,
    ...proof(c),
  });

export const changeUserPassword = (
  id: string,
  newPassword: string,
  c: Confirmation,
) =>
  send<{ sessions_revoked: number }>(`/api/auth/users/${id}/password`, "POST", {
    new_password: newPassword,
    ...proof(c),
  });

export const sendResetLink = (id: string) =>
  send<{ status: string }>(`/api/auth/users/${id}/password-reset-link`, "POST");

export const forcePasswordReset = (id: string, c: Confirmation) =>
  send<{ sessions_revoked: number }>(
    `/api/auth/users/${id}/force-password-reset`,
    "POST",
    proof(c),
  );

export const listEmailDestinations = (id: string) =>
  send<EmailDestination[]>(`/api/auth/users/${id}/email-destinations`, "GET");

export const requestEmailVerification = (id: string, destinationId?: string) =>
  send<{ status: string }>(`/api/auth/users/${id}/email-verification`, "POST", {
    destination_id: destinationId ?? null,
  });

export const revokeEmailVerification = (
  id: string,
  destinationId: string,
  confirmUsername: string,
) =>
  send<{ status: string }>(
    `/api/auth/users/${id}/email-verification/revoke`,
    "POST",
    { destination_id: destinationId, confirm_username: confirmUsername },
  );

export const listUserAudit = (id: string) =>
  send<AuditEntry[]>(`/api/auth/user-audit/${id}`, "GET");
