import { attachGameAssetFromUrl, uploadGameAsset } from "../services/games";
import {
  startTask,
  updateTask,
  completeTask,
  errorTask,
  setTaskRetry,
} from "../state/taskProgress";
import type { StagedArtwork } from "./useStagedArtwork";

// Artwork staged in an editor, sent after the game itself is saved. Shared by
// the full and the quick game editors so both report a partial success and
// retry the same way.
export interface ArtworkTransfer {
  kind: "key_art" | "banner";
  label: string;
  file: File | null;
  url: string | null;
}

export function stagedArtworkList(
  roles: {
    kind: ArtworkTransfer["kind"];
    label: string;
    choice: StagedArtwork | null;
  }[],
): ArtworkTransfer[] {
  return roles.flatMap(({ kind, label, choice }) =>
    choice
      ? [
          choice.source === "file"
            ? { kind, label, file: choice.file, url: null }
            : { kind, label, file: null, url: choice.url },
        ]
      : [],
  );
}

// A failure leaves the game saved and offers a retry of the remaining
// artwork on that same game only.
export async function attachArtwork(
  gameId: string,
  gameTitle: string,
  artwork: ArtworkTransfer[],
) {
  const taskId = startTask(`Artwork for ${gameTitle}`, artwork.length);
  let done = 0;
  let running = false;
  const attempt = async () => {
    if (running || done === artwork.length) return;
    running = true;
    try {
      while (done < artwork.length) {
        const asset = artwork[done]!;
        if (asset.file) await uploadGameAsset(gameId, asset.kind, asset.file);
        else if (asset.url)
          await attachGameAssetFromUrl(gameId, asset.kind, asset.url);
        updateTask(taskId, ++done);
      }
      completeTask(taskId, "Artwork saved.");
    } catch {
      errorTask(
        taskId,
        `Game saved. Artwork could not be attached (${artwork[done]!.label}). Retry artwork or edit it later.`,
      );
      setTaskRetry(taskId, () => void attempt());
    } finally {
      running = false;
    }
  };
  await attempt();
}
