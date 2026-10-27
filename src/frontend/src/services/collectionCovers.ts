import { ref } from "vue";
import { currentUser } from "../state/auth";

// A local revision changes the URL immediately after an upload or removal.
// The server supplies ETags and revalidates mutable URLs on later visits.
export const collectionCoverRevisions = ref<Record<string, string>>({});

export function orderCollectionCoverGames<T extends { id: string }>(
  games: readonly T[],
  savedOrder: readonly string[] | undefined,
  pickedId: string | undefined,
): T[] {
  const byId = new Map(games.map((game) => [game.id, game]));
  const ordered = (savedOrder ?? []).flatMap((id) => {
    const game = byId.get(id);
    byId.delete(id);
    return game ? [game] : [];
  });
  ordered.push(...byId.values());
  const pickedIndex = pickedId
    ? ordered.findIndex((game) => game.id === pickedId)
    : -1;
  if (pickedIndex > 0) ordered.unshift(...ordered.splice(pickedIndex, 1));
  return ordered;
}

export function collectionCoverUrl(
  name: string,
  gameIds: readonly string[],
): string {
  const params = new URLSearchParams({ name });
  if (currentUser.value?.id) params.set("owner", currentUser.value.id);
  gameIds.slice(0, 4).forEach((id) => params.append("ids", id));
  const revision = collectionCoverRevisions.value[name];
  if (revision) params.set("v", revision);
  return `/api/game/collection-cover?${params}`;
}

export async function uploadCollectionCover(
  name: string,
  file: File,
): Promise<void> {
  const body = new FormData();
  body.append("name", name);
  body.append("file", file);
  const response = await fetch("/api/game/collection-cover", {
    method: "PUT",
    credentials: "include",
    body,
  });
  if (!response.ok) throw new Error("Could not upload the collection cover.");
  const result = (await response.json()) as { revision: string };
  collectionCoverRevisions.value = {
    ...collectionCoverRevisions.value,
    [name]: result.revision,
  };
}

export async function removeCollectionCover(name: string): Promise<void> {
  const response = await fetch(
    `/api/game/collection-cover?${new URLSearchParams({ name })}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) throw new Error("Could not remove the collection cover.");
  collectionCoverRevisions.value = {
    ...collectionCoverRevisions.value,
    [name]: String(Date.now()),
  };
}

export async function renameCollectionCover(
  oldName: string,
  newName: string,
): Promise<void> {
  if (oldName === newName) return;
  const response = await fetch("/api/game/collection-cover/rename", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_name: oldName, new_name: newName }),
  });
  if (!response.ok) throw new Error("Could not move the collection cover.");
  const revisions = { ...collectionCoverRevisions.value };
  revisions[newName] = revisions[oldName] ?? String(Date.now());
  delete revisions[oldName];
  collectionCoverRevisions.value = revisions;
}
