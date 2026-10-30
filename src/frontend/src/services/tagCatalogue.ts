import { failedRequest } from "./apiError";
import { setCatalogueRules } from "../utils/genres";

// Private and deployment-default tag labels and aliases (#267). The catalogue
// only changes how tags are labelled and grouped; the tags stored on games and
// media are never rewritten by any of these calls.

export interface TagAlias {
  id: string;
  label: string;
}

export interface CatalogueTag {
  id: string;
  label: string;
  key: string;
  kind: "user" | "source";
  aliases: TagAlias[];
}

export interface TagCatalogue {
  tags: CatalogueTag[];
  defaults: CatalogueTag[];
  suppressed: string[];
}

export interface TagUsage {
  tag: string;
  uses: number;
  label: string;
}

export interface AliasSuggestion {
  tag: string;
  alias: string;
  uses: [number, number];
}

async function send<T>(
  url: string,
  method: string,
  body?: unknown,
): Promise<T | undefined> {
  const response = await fetch(url, {
    method,
    credentials: "include",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failedRequest(response);
  return response.status === 204 ? undefined : ((await response.json()) as T);
}

/** The folding the server uses to compare spellings, for hints before a request. */
export function foldTagKey(raw: string): string {
  return raw
    .normalize("NFKC")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export async function fetchTagCatalogue(): Promise<TagCatalogue> {
  return (await send<TagCatalogue>("/api/tags", "GET"))!;
}

export async function fetchTagUsage(): Promise<TagUsage[]> {
  return (await send<{ tags: TagUsage[] }>("/api/tags/usage", "GET"))!.tags;
}

export async function fetchAliasSuggestions(): Promise<AliasSuggestion[]> {
  return (await send<{ suggestions: AliasSuggestion[] }>(
    "/api/tags/suggestions",
    "GET",
  ))!.suggestions;
}

export async function discoverTags(): Promise<number> {
  return (await send<{ created: number }>("/api/tags/discover", "POST"))!
    .created;
}

export async function createTag(name: string): Promise<CatalogueTag> {
  return (await send<CatalogueTag>("/api/tags", "POST", { name }))!;
}

export async function renameTag(
  id: string,
  name: string,
): Promise<CatalogueTag> {
  return (await send<CatalogueTag>(`/api/tags/${id}`, "PATCH", { name }))!;
}

export async function deleteTag(id: string): Promise<void> {
  await send(`/api/tags/${id}`, "DELETE");
}

export async function addTagAlias(id: string, name: string): Promise<TagAlias> {
  return (await send<TagAlias>(`/api/tags/${id}/aliases`, "POST", { name }))!;
}

export async function removeTagAlias(
  id: string,
  aliasId: string,
): Promise<void> {
  await send(`/api/tags/${id}/aliases/${aliasId}`, "DELETE");
}

export async function setTagSuppressed(
  tag: string,
  suppressed: boolean,
): Promise<void> {
  await send("/api/tags/suppressions", "PUT", { tag, suppressed });
}

// Administrator-managed deployment defaults.

export async function createDefaultTag(name: string): Promise<CatalogueTag> {
  return (await send<CatalogueTag>("/api/tags/defaults", "POST", { name }))!;
}

export async function renameDefaultTag(
  id: string,
  name: string,
): Promise<CatalogueTag> {
  return (await send<CatalogueTag>(`/api/tags/defaults/${id}`, "PATCH", {
    name,
  }))!;
}

export async function deleteDefaultTag(id: string): Promise<void> {
  await send(`/api/tags/defaults/${id}`, "DELETE");
}

export async function addDefaultAlias(
  id: string,
  name: string,
): Promise<TagAlias> {
  return (await send<TagAlias>(`/api/tags/defaults/${id}/aliases`, "POST", {
    name,
  }))!;
}

export async function removeDefaultAlias(
  id: string,
  aliasId: string,
): Promise<void> {
  await send(`/api/tags/defaults/${id}/aliases/${aliasId}`, "DELETE");
}

/** Fetch the person's effective rules and hand them to the tag pickers and filters. */
export async function loadCatalogueRules(): Promise<void> {
  const body = await send<{ rules: Record<string, { label: string }> }>(
    "/api/tags/resolution",
    "GET",
  );
  setCatalogueRules(
    Object.fromEntries(
      Object.entries(body?.rules ?? {}).map(([key, rule]) => [key, rule.label]),
    ),
  );
}
