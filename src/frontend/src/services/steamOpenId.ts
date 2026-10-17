// Steam OpenID 2.0 redirect sign-in. It proves which SteamID the person
// controls; it does not give the app a Steam session or private-data access.
export const STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login";

export async function beginSteamOpenId(): Promise<string> {
  const response = await fetch("/api/settings/steam/openid/begin", {
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data ? data.detail : null;
    throw new Error(
      typeof detail === "string"
        ? detail
        : "Couldn't start Steam sign-in. Try again.",
    );
  }
  const url =
    data && typeof data === "object" && "redirect_url" in data
      ? data.redirect_url
      : null;
  // Only ever send the browser to Steam's own sign-in page.
  if (typeof url !== "string" || !url.startsWith(`${STEAM_OPENID_ENDPOINT}?`))
    throw new Error("The server returned an invalid Steam sign-in address.");
  return url;
}

export function steamOpenIdOutcome(query: {
  [key: string]: unknown;
}): { ok: boolean; message: string } | null {
  const first = (value: unknown) => (Array.isArray(value) ? value[0] : value);
  const status = first(query.steam_openid);
  if (status === "linked")
    return {
      ok: true,
      message:
        "Steam account verified. Add a Web API key or sign in to Steam to import your library.",
    };
  if (status !== "failed") return null;
  const reason = first(query.steam_openid_error);
  return {
    ok: false,
    message:
      typeof reason === "string" && reason
        ? reason.slice(0, 300)
        : "Steam sign-in could not be verified. Try again.",
  };
}
