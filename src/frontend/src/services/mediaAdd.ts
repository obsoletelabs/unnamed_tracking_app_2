import { createMovie } from "./movies";
import { createTVShow, updateSeason as updateTVSeason } from "./tvShows";
import { createAnime, updateSeason as updateAnimeSeason } from "./anime";
import type { MetadataCandidate } from "./metadata";
import type { QuickAddForm } from "../types/mediaLibrary";
import type { MovieStatus } from "../types/movie";
import type { TVShowStatus } from "../types/tv_show";
import type { AnimeStatus } from "../types/anime";
import {
  movieMetadataResult,
  tvMetadataResult,
  animeMetadataResult,
} from "../utils/metadataCandidate";

export function metadataEpisodeTotal(
  candidate: MetadataCandidate,
): number | null {
  if (candidate.media_type === "movie") return null;
  if (candidate.metadata.episode_count != null)
    return candidate.metadata.episode_count;
  const seasons = candidate.metadata.seasons ?? [];
  return seasons.length &&
    seasons.every((season) => season.episode_count != null)
    ? seasons.reduce((sum, season) => sum + (season.episode_count ?? 0), 0)
    : null;
}

export async function addMediaCandidate(
  candidate: MetadataCandidate,
  form: QuickAddForm,
) {
  const common = {
    title: candidate.metadata.title || candidate.title,
    providerIds: candidate.provider_ids,
    description: candidate.metadata.description ?? null,
    studios: candidate.metadata.studios ?? [],
    countries: candidate.metadata.countries ?? [],
    languages: candidate.metadata.languages ?? [],
    genres: candidate.metadata.genres ?? [],
    tags: candidate.metadata.tags ?? [],
    features: candidate.metadata.features ?? [],
    ageRating: candidate.metadata.age_rating ?? null,
    ratingOverall: form.score,
    startDate: form.startDate,
    endDate: form.endDate,
  };
  if (candidate.media_type === "movie") {
    const metadata = movieMetadataResult(candidate);
    const movie = await createMovie({
      ...common,
      status: form.status as MovieStatus,
      releaseDate: metadata.releaseDate,
      runtimeMinutes: metadata.runtimeMinutes,
      director: metadata.director,
      writer: metadata.writer,
      posterUrl: metadata.posterUrl,
      backdropUrl: metadata.backdropUrl,
      tmdbScore: metadata.tmdbScore,
    });
    return { path: `/movies/${movie.id}`, progressWarning: false };
  }
  if (candidate.media_type !== "tv_show" && candidate.media_type !== "anime")
    throw new Error("Choose a movie, TV show or anime result.");
  const total = metadataEpisodeTotal(candidate);
  const show =
    candidate.media_type === "tv_show"
      ? await (() => {
          const metadata = tvMetadataResult(candidate);
          return createTVShow({
            ...common,
            status: form.status as TVShowStatus,
            firstAirDate: metadata.firstAirDate,
            episodeRuntimeMinutes: metadata.episodeRuntimeMinutes,
            creators: metadata.creators,
            posterUrl: metadata.posterUrl,
            backdropUrl: metadata.backdropUrl,
            tmdbScore: metadata.tmdbScore,
            externalId: metadata.tvmazeId,
            seasons: metadata.seasons.length
              ? metadata.seasons
              : [{ seasonNumber: 1, episodeCount: total }],
          });
        })()
      : await (() => {
          const metadata = animeMetadataResult(candidate);
          return createAnime({
            ...common,
            status: form.status as AnimeStatus,
            firstAirDate: metadata.firstAirDate,
            episodeRuntimeMinutes: metadata.episodeRuntimeMinutes,
            posterUrl: metadata.posterUrl,
            backdropUrl: metadata.backdropUrl,
            format: metadata.format,
            anilistScore: metadata.anilistScore,
            malScore: metadata.malScore,
            externalId: metadata.malId,
            anilistId: candidate.provider_ids.anilist ?? null,
            seasons: [{ seasonNumber: 1, episodeCount: total }],
          });
        })();
  const updateSeason =
    candidate.media_type === "tv_show" ? updateTVSeason : updateAnimeSeason;
  let remaining = form.watched;
  let progressWarning = false;
  for (const season of [...show.seasons].sort(
    (a, b) => a.seasonNumber - b.seasonNumber,
  )) {
    if (remaining <= 0) break;
    const watched =
      season.episodeCount == null
        ? remaining
        : Math.min(remaining, season.episodeCount);
    if (watched <= 0) continue;
    try {
      await updateSeason(show.id, season.id, { episodesWatched: watched });
    } catch {
      progressWarning = true;
      break; // The title is already saved; retrying the whole add would duplicate it.
    }
    remaining -= watched;
  }
  return {
    path: `/${candidate.media_type === "tv_show" ? "tv" : "anime"}/${show.id}`,
    progressWarning: progressWarning || remaining > 0,
  };
}
