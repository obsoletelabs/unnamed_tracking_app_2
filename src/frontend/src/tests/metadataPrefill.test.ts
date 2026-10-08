import { expect, it } from "vitest";
import { metadataPrefill } from "../utils/metadataPrefill";
import {
  movieMetadataResult,
  animeMetadataResult,
} from "../utils/metadataCandidate";
import type { MetadataCandidate } from "../services/metadata";

it("retains partial fields and manual edits while late enrichment fills blanks", () => {
  const fill = metadataPrefill<{
    title: string;
    description: string;
    poster: string;
  }>();
  const fields = { title: "", description: "", poster: "" };
  fill(fields, { title: "Portal" }, "one");
  fields.title = "My Portal";
  fill(
    fields,
    { title: "Portal 2", description: "A puzzle game", poster: "cover" },
    "one",
  );
  expect(fields).toEqual({
    title: "My Portal",
    description: "A puzzle game",
    poster: "cover",
  });
  fill(fields, { description: "", poster: "" }, "one");
  expect(fields.description).toBe("A puzzle game");
  fields.poster = "my upload";
  fill(fields, { poster: "new provider cover" }, "one");
  expect(fields.poster).toBe("my upload");
});

it("keeps a known year without inventing a date and converts canonical scores", () => {
  const candidate: MetadataCandidate = {
    id: "one",
    external_id: "12",
    title: "Known title",
    year: 2011,
    provider: "test.provider",
    provider_name: "Test",
    providers: ["test.provider"],
    rank: 0,
    alternate_titles: [],
    media_type: "movie",
    assets: [],
    provider_ids: { tmdb: "12", anilist: "34" },
    metadata: { scores: { tmdb: 82, anilist: 86 } },
  };
  const movie = movieMetadataResult(candidate);
  expect(movie.releaseYear).toBe(2011);
  expect(movie.releaseDate).toBeNull();
  expect(movie.tmdbScore).toBe(8.2);
  expect(movie.providerIds).toEqual(candidate.provider_ids);
  expect(animeMetadataResult(candidate).anilistScore).toBe(8.6);
});
