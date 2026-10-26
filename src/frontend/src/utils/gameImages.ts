export type GameAssetKind = "key_art" | "banner" | "logo" | "icon";

// Artwork for a game comes from /api/game/<id>/assets/<kind>. The server names
// each stored file by a revision of its bytes (an upload answers with it, and
// every image response carries it as its ETag). Adding it as ?v= lets a browser
// keep that exact picture indefinitely; without it the plain URL still works and
// is simply revalidated. A revision is never a clock reading made in the browser.
const knownRevisions = new Map<string, string>();

export function rememberArtworkRevision(
  gameId: string,
  kind: GameAssetKind,
  revision: unknown,
): void {
  if (typeof revision === "string" && /^[0-9a-f]{8,64}$/.test(revision)) {
    knownRevisions.set(`${gameId}/${kind}`, revision);
  }
}

// Records the revision an upload or download answered with.
export function rememberUploadedRevision<T extends { revision?: string }>(
  gameId: string,
  kind: GameAssetKind,
  result: T,
): T {
  rememberArtworkRevision(gameId, kind, result.revision);
  return result;
}

export function gameAssetUrl(gameId: string, kind: GameAssetKind): string {
  const base = `/api/game/${gameId}/assets/${kind}`;
  const revision = knownRevisions.get(`${gameId}/${kind}`);
  return revision ? `${base}?v=${revision}` : base;
}

// A banner is a 3840 px PNG of several MB, so a page asks for a copy no wider
// than it shows.
export function sizedAssetUrl(url: string, width: number): string {
  if (!url.startsWith("/api/")) return url;
  // Replace the data-layer default when a component knows its actual display
  // size (e.g. a small list thumbnail vs. a detail poster).
  if (/[?&]w=\d+/.test(url))
    return url.replace(/([?&])w=\d+/, `$1w=${width}`);
  return `${url}${url.includes("?") ? "&" : "?"}w=${width}`;
}

// Keep broken or missing artwork visually stable and useful at every library
// density. The data URL avoids an extra asset request for each fallback.
export function showGameCoverPlaceholder(event: Event, title: string): void {
  const image = event.currentTarget;
  if (!(image instanceof HTMLImageElement) || image.dataset.fallback) return;
  image.dataset.fallback = "true";
  const label = title.trim().slice(0, 36) || "No artwork";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 600"><defs><pattern id="h" width="18" height="18" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0 0V18" stroke="#8790a8" stroke-opacity=".16" stroke-width="5"/></pattern></defs><rect width="400" height="600" fill="#252b39"/><rect width="400" height="600" fill="url(#h)"/><rect x="24" y="24" width="352" height="552" rx="18" fill="none" stroke="#8790a8" stroke-opacity=".25"/><text x="200" y="310" fill="#d8deeb" font-family="system-ui,sans-serif" font-size="22" font-weight="600" text-anchor="middle">${label.replace(/[&<>\"]/g, "")}</text><text x="200" y="348" fill="#8790a8" font-family="system-ui,sans-serif" font-size="14" text-anchor="middle">ARTWORK MISSING</text></svg>`;
  image.src = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// The hero is about as wide as a big screen, so this is plenty for it.
export const HERO_WIDTH = 1920;
// A compact default for library cards. Detail views ask for a larger image
// explicitly, avoiding 400px art for every small shelf/list thumbnail.
export const POSTER_WIDTH = 240;

// Starts downloading an image so it is already there when the page asks.
export function preloadImage(url: string): void {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return;
  const image = new Image();
  image.src = url;
}
