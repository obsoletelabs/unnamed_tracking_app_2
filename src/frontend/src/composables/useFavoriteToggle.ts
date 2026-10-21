import { ref } from "vue";
import { setFavorite } from "../services/games";

/**
 * Optimistic favourite toggle for the game on screen: the star flips at once,
 * is put back if saving fails (with a message), and a second click while the
 * first is saving is ignored so the two cannot race.
 */
export function useFavoriteToggle(
  current: () => { id: string; favorite: boolean } | null | undefined,
) {
  const favoriteError = ref<string | null>(null);
  let saving = false;

  async function toggleFavorite() {
    const target = current();
    if (!target || saving) return;
    saving = true;
    favoriteError.value = null;
    const next = !target.favorite;
    target.favorite = next;
    try {
      await setFavorite(target.id, next);
    } catch {
      target.favorite = !next;
      favoriteError.value = "Could not update the favourite. Try again.";
    } finally {
      saving = false;
    }
  }

  return { favoriteError, toggleFavorite };
}
