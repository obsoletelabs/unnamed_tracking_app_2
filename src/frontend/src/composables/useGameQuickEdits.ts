import { ref, watch, type Ref } from "vue";
import { setRatings, setStatus, type GameRatings } from "../services/games";
import type { Game, GameStatus } from "../types/game";
import { orderedFieldSave } from "./useOrderedFieldSave";

/**
 * The game page's one-click status and rating changes. They apply at once and
 * are saved one at a time per field and game, so two quick changes cannot
 * overwrite each other out of order. When the newest save fails, the field
 * returns to the last value the server accepted, on the game it was made for,
 * and `quickEditError` says so while that game is shown.
 */
export function useGameQuickEdits(game: Ref<Game | null>) {
  const quickEditError = ref<string | null>(null);
  watch(
    () => game.value?.id,
    () => (quickEditError.value = null),
  );

  function fieldSaver<T>(
    read: (target: Game) => T,
    write: (target: Game, value: T) => void,
    save: (gameId: string, value: T) => Promise<unknown>,
    failure: string,
  ) {
    const queued = orderedFieldSave<Game, T>({
      read,
      write,
      save: (target, value) => save(target.id, value),
      onFailure: (target) => {
        if (game.value === target) quickEditError.value = failure;
      },
    });
    return (target: Game, value: T) => {
      if (game.value === target) quickEditError.value = null;
      return queued(target, value);
    };
  }

  const saveStatus = fieldSaver<GameStatus>(
    (target) => target.status,
    (target, value) => (target.status = value),
    setStatus,
    "Could not change the status. Try again.",
  );
  async function changeStatus(next: GameStatus) {
    if (!game.value || next === game.value.status) return;
    await saveStatus(game.value, next);
  }

  const saveRatings = fieldSaver<GameRatings>(
    (target) => ({
      ratingOverall: target.ratingOverall,
      ratingStory: target.ratingStory,
      ratingGameplay: target.ratingGameplay,
      ratingSound: target.ratingSound,
    }),
    (target, value) => Object.assign(target, value),
    setRatings,
    "Could not save the rating. Try again.",
  );
  async function onRatingsChange(ratings: GameRatings) {
    if (!game.value) return;
    await saveRatings(game.value, ratings);
  }

  return { quickEditError, changeStatus, onRatingsChange };
}
