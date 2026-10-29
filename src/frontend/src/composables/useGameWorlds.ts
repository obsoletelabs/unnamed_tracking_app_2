import { ref } from "vue";
import {
  createArchive,
  addArchiveVersion,
  fetchArchives,
} from "../services/gameArchives";
import type { GameArchiveData } from "../services/gameArchives";
import {
  startTask,
  updateTask,
  completeTask,
  errorTask,
  setTaskRetry,
} from "../state/taskProgress";
import type { Game } from "../types/game";
import { usePrompt } from "../state/dialog";
import type { Ref } from "vue";
export function useGameWorlds(
  game: Ref<Game | null>,
  saveUploading: Ref<Set<string>>,
  filesError: Ref<string | null>,
  prompt: ReturnType<typeof usePrompt>,
) {
  // Named, versioned world-save archives use the same API as Saves.
  const worlds = ref<GameArchiveData[]>([]);
  const worldsLoaded = ref(false);

  async function refreshWorlds() {
    if (!game.value) return;
    const gameId = game.value.id;
    filesError.value = null;
    try {
      const archives = await fetchArchives(gameId, "world_save", true);
      if (game.value?.id !== gameId) return;
      worlds.value = archives;
      worldsLoaded.value = true;
    } catch (err) {
      if (game.value?.id !== gameId) return;
      filesError.value =
        err instanceof Error ? err.message : "Failed to load worlds";
    }
  }

  async function onNewWorldSelected(files: File[]) {
    const file = files[0];
    if (!file || !game.value) return;
    const gameId = game.value.id;
    const name = await prompt({
      title: "Name this world",
      message: "World name",
      defaultValue: file.name.replace(/\.[^.]+$/, ""),
      confirmLabel: "Upload",
    });
    if (!name || !name.trim()) return;
    const trimmedName = name.trim();
    const taskId = startTask(`Uploading "${trimmedName}"`, 100);

    const attempt = async () => {
      saveUploading.value = new Set(saveUploading.value).add("");
      try {
        await createArchive(
          gameId,
          "world_save",
          trimmedName,
          file,
          (f, speedLabel) =>
            updateTask(taskId, Math.round(f * 100), undefined, speedLabel),
        );
        completeTask(taskId, "Saved");
        await refreshWorlds();
      } catch (err) {
        errorTask(taskId, err instanceof Error ? err.message : "Upload failed");
        setTaskRetry(taskId, () => void attempt());
      } finally {
        const next = new Set(saveUploading.value);
        next.delete("");
        saveUploading.value = next;
      }
    };
    await attempt();
  }

  async function onAddWorldVersion(archive: GameArchiveData, files: File[]) {
    const file = files[0];
    if (!file || !game.value) return;
    const gameId = archive.game_id ?? game.value.id;
    const taskId = startTask(`Uploading new version of "${archive.name}"`, 100);

    const attempt = async () => {
      saveUploading.value = new Set(saveUploading.value).add(archive.id);
      try {
        await addArchiveVersion(gameId, archive.id, file, (f, speedLabel) =>
          updateTask(taskId, Math.round(f * 100), undefined, speedLabel),
        );
        completeTask(taskId, "Saved");
        await refreshWorlds();
      } catch (err) {
        errorTask(taskId, err instanceof Error ? err.message : "Upload failed");
        setTaskRetry(taskId, () => void attempt());
      } finally {
        const next = new Set(saveUploading.value);
        next.delete(archive.id);
        saveUploading.value = next;
      }
    };
    await attempt();
  }

  return {
    worlds,
    worldsLoaded,
    refreshWorlds,
    onNewWorldSelected,
    onAddWorldVersion,
  };
}
