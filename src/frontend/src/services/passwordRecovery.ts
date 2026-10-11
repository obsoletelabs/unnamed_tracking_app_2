export async function passwordResetStatus(): Promise<boolean> {
  const response = await fetch("/api/auth/password-reset/status");
  if (!response.ok) return false;
  const data = (await response.json()) as { enabled: boolean };
  return data.enabled === true;
}

async function submit(
  path: string,
  payload: Record<string, string>,
): Promise<string> {
  const response = await fetch(`/api/auth/password-reset/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await response.json()) as {
    message?: string;
    detail?: unknown;
  };
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Unable to complete this request.",
    );
  return data.message ?? "Request completed.";
}

export function requestPasswordReset(identifier: string): Promise<string> {
  return submit("request", { identifier });
}

export function completePasswordReset(
  id: string,
  token: string,
  password: string,
): Promise<string> {
  return submit("complete", { id, token, password });
}

export function authLinkCredential(
  fragment: string,
): { id: string; token: string } | null {
  const values = new URLSearchParams(fragment.replace(/^#/, ""));
  const id = values.get("id");
  const token = values.get("token");
  if (
    !id ||
    !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(id) ||
    !token ||
    !/^[\w-]{32,128}$/.test(token)
  )
    return null;
  return { id, token };
}

export const resetCredential = authLinkCredential;
