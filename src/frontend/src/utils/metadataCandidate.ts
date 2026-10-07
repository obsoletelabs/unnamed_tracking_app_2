import type { MetadataCandidate } from "../services/metadata";
import type { MetadataSearchResult } from "../services/games";
import type { MovieMetadataResult } from "../services/movies";
import type { TVShowMetadataResult } from "../services/tvShows";
import type { AnimeMetadataResult } from "../services/anime";

function score(value: number | undefined): number | null {
  return value === undefined ? null : value / 10;
}

function mediaIdentity(candidate: MetadataCandidate) {
  const metadata = candidate.metadata;
  return {
    candidateId: candidate.id,
    providerIds: candidate.provider_ids,
    provider: candidate.provider_name,
    providerId: candidate.external_id,
    title: metadata.title || candidate.title,
    releaseYear: metadata.year ?? candidate.year,
    description: metadata.description ?? null,
    studios: metadata.studios ?? [],
    countries: metadata.countries ?? [],
    genres: metadata.genres ?? [],
    posterUrl:
      candidate.assets.find((asset) => asset.kind === "poster")?.url ?? null,
    backdropUrl:
      candidate.assets.find((asset) => ["banner", "hero"].includes(asset.kind))
        ?.url ?? null,
    url: metadata.links?.[0]?.url ?? null,
  };
}

export function movieMetadataResult(
  candidate: MetadataCandidate,
): MovieMetadataResult {
  return {
    ...mediaIdentity(candidate),
    releaseDate: candidate.metadata.release_date ?? null,
    runtimeMinutes: candidate.metadata.runtime_minutes ?? null,
    director: candidate.metadata.director ?? null,
    writer: candidate.metadata.writer ?? null,
    languages: candidate.metadata.languages ?? [],
    tmdbScore: score(candidate.metadata.scores?.tmdb),
  };
}

export function tvMetadataResult(
  candidate: MetadataCandidate,
): TVShowMetadataResult {
  return {
    ...mediaIdentity(candidate),
    firstAirDate: candidate.metadata.release_date ?? null,
    episodeRuntimeMinutes: candidate.metadata.episode_runtime_minutes ?? null,
    creators: candidate.metadata.creators ?? [],
    languages: candidate.metadata.languages ?? [],
    tmdbScore: score(candidate.metadata.scores?.tmdb),
    tvmazeId: candidate.provider_ids.tvmaze ?? null,
    seasons: (candidate.metadata.seasons ?? []).map((season) => ({
      seasonNumber: season.season_number,
      name: season.title,
      episodeCount: season.episode_count,
      airDate: season.air_date,
      posterUrl: null,
    })),
  };
}

export function animeMetadataResult(
  candidate: MetadataCandidate,
): AnimeMetadataResult {
  return {
    ...mediaIdentity(candidate),
    firstAirDate: candidate.metadata.release_date ?? null,
    episodeRuntimeMinutes: candidate.metadata.episode_runtime_minutes ?? null,
    episodeCount: candidate.metadata.episode_count ?? null,
    format: candidate.metadata.format ?? null,
    anilistScore: score(candidate.metadata.scores?.anilist),
    malScore: score(candidate.metadata.scores?.mal),
    malId: candidate.provider_ids.mal ?? null,
  };
}

export function gameMetadataResult(
  candidate: MetadataCandidate,
): MetadataSearchResult {
  const metadata = candidate.metadata;
  const assets = (kind: string) =>
    candidate.assets
      .filter((asset) => asset.kind === kind)
      .map((asset) => asset.url);
  const keyArt = assets("key_art");
  const banners = assets("banner");
  const logos = assets("logo");
  const icons = assets("icon");
  return {
    candidate_id: candidate.id,
    provider_ids: candidate.provider_ids,
    provider: candidate.provider_name,
    provider_id: candidate.external_id,
    title: metadata.title || candidate.title,
    description: metadata.description ?? null,
    release_date: metadata.release_date ?? null,
    release_year: metadata.year ?? candidate.year,
    developer: metadata.developer ?? null,
    publisher: metadata.publisher ?? null,
    series: metadata.series ?? null,
    age_rating: metadata.age_rating ?? null,
    time_to_beat_hours: metadata.time_to_beat_hours ?? null,
    tags: metadata.tags ?? metadata.genres ?? [],
    features: metadata.features ?? [],
    links: metadata.links ?? [],
    key_art_url: keyArt[0] ?? null,
    key_art_urls: keyArt,
    banner_url: banners[0] ?? null,
    banner_urls: banners,
    logo_url: logos[0] ?? null,
    logo_urls: logos,
    icon_url: icons[0] ?? null,
    icon_urls: icons,
  };
}
