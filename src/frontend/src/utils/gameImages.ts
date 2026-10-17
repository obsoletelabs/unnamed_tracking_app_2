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
  // already sized, or not one of ours
  if (!url.startsWith("/api/") || /[?&]w=/.test(url)) return url;
  return `${url}${url.includes("?") ? "&" : "?"}w=${width}`;
}

// The hero is about as wide as a big screen, so this is plenty for it.
export const HERO_WIDTH = 1920;
// The poster is 190 px wide on the page; twice that keeps it sharp on a retina screen.
export const POSTER_WIDTH = 400;

// Starts downloading an image so it is already there when the page asks.
export function preloadImage(url: string): void {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return;
  const image = new Image();
  image.src = url;
}
