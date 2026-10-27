import { ref } from "vue";
import { currentUser } from "../state/auth";

const revisions = ref<Record<string, string>>({});
const key = (listId: string) => `${currentUser.value?.id ?? ""}/${listId}`;

export function mediaCollectionCoverImage(
  list: { id: string; coverUrl?: string | null },
  revision?: number,
): string | undefined {
  if (!list.coverUrl) return undefined;
  const params = new URLSearchParams(list.coverUrl.split("?")[1]);
  const local = revisions.value[key(list.id)];
  if (local) params.set("r", local);
  if (revision) params.set("view", String(revision));
  return `${list.coverUrl.split("?")[0]}?${params}`;
}

export async function uploadMediaCollectionCover(
  listId: string,
  file: File,
): Promise<void> {
  const ownerKey = key(listId);
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(
    `/api/lists/${encodeURIComponent(listId)}/cover`,
    {
      method: "PUT",
      credentials: "include",
      body,
    },
  );
  if (!response.ok) throw new Error("Could not upload the collection cover.");
  const result = (await response.json()) as { revision: string };
  revisions.value = { ...revisions.value, [ownerKey]: result.revision };
}

export async function removeMediaCollectionCover(
  listId: string,
): Promise<void> {
  const ownerKey = key(listId);
  const response = await fetch(
    `/api/lists/${encodeURIComponent(listId)}/cover`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  if (!response.ok) throw new Error("Could not remove the collection cover.");
  revisions.value = { ...revisions.value, [ownerKey]: String(Date.now()) };
}
