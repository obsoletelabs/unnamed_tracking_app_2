import { failedRequest } from "./apiError";
import { rememberGameRelationship } from "./games";

export async function setOwnedCopy(
  mainId: string,
  copyId: string,
  grouped: boolean,
): Promise<void> {
  const response = await fetch(
    `/api/game-ownership/${grouped ? "group" : "separate"}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ main_id: mainId, copy_id: copyId }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  rememberGameRelationship(
    copyId,
    grouped ? mainId : null,
    grouped ? "owned_copy" : null,
  );
}
