// Links to a title's page on the metadata sites the app already knows it by. Only identifiers
// that match a strict shape become links, and only for the fixed hosts below.
import type { MediaType } from "../services/mediaExtras";

export interface ProviderLink {
  id: string;
  label: string;
  url: string;
}

const NUMERIC = /^[0-9]{1,12}$/;
const IMDB = /^tt[0-9]{5,12}$/;

export function providerLinks(
  mediaType: MediaType,
  providerIds: Record<string, string> | undefined,
): ProviderLink[] {
  const ids = providerIds ?? {};
  const value = (key: string) => (ids[key] ?? "").trim();
  const links: ProviderLink[] = [];
  const add = (id: string, label: string, url: string) =>
    links.push({ id, label, url });

  if (mediaType === "anime") {
    const anilist = value("anilist");
    if (NUMERIC.test(anilist))
      add("anilist", "AniList", `https://anilist.co/anime/${anilist}`);
    const mal = value("mal");
    if (NUMERIC.test(mal))
      add("mal", "MyAnimeList", `https://myanimelist.net/anime/${mal}`);
    const kitsu = value("kitsu");
    if (NUMERIC.test(kitsu))
      add("kitsu", "Kitsu", `https://kitsu.app/anime/${kitsu}`);
  }
  if (mediaType === "tv") {
    const tvmaze = value("tvmaze");
    if (NUMERIC.test(tvmaze))
      add("tvmaze", "TVmaze", `https://www.tvmaze.com/shows/${tvmaze}`);
  }
  // Anime keep separate TMDB identities for series and films.
  const tmdbKind =
    mediaType === "movie" ? "movie" : mediaType === "tv" ? "tv" : null;
  const tmdb =
    tmdbKind !== null
      ? value("tmdb")
      : value("tmdb.tv") || value("tmdb.movie") || "";
  const animeKind = value("tmdb.tv") ? "tv" : "movie";
  if (NUMERIC.test(tmdb))
    add(
      "tmdb",
      "TMDB",
      `https://www.themoviedb.org/${tmdbKind ?? animeKind}/${tmdb}`,
    );
  const imdb = value("imdb");
  if (IMDB.test(imdb))
    add("imdb", "IMDb", `https://www.imdb.com/title/${imdb}/`);
  return links;
}
