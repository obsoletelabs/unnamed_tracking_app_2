export async function invitationStatus(): Promise<boolean> {
  const response = await fetch("/api/auth/user-invites/status");
  // A failed check is not "disabled": callers tell the two apart and offer a retry.
  if (!response.ok) throw new Error("Could not check availability.");
  const data = (await response.json()) as { enabled: boolean };
  return data.enabled === true;
}

export async function acceptInvitation(
  id: string,
  token: string,
  username: string,
  password: string,
): Promise<string> {
  const response = await fetch("/api/auth/user-invites/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, token, username, password }),
  });
  const data = (await response.json()) as {
    message?: string;
    detail?: unknown;
  };
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Unable to accept this invitation.",
    );
  return (
    data.message ?? "Account created. Sign in with your username and password."
  );
}
