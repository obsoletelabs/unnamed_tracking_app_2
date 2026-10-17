import type { GameStatus } from "../types/game";
import type { ArtworkChoice } from "../composables/useStagedArtwork";

// Unsaved values the game quick editor carries into the full editor, so
// "Edit more" never discards what was just typed or chosen. The rating stays
// as typed (a number or text), and is validated again by the editor that saves.
export interface GameEditDraft {
  status: GameStatus;
  platform: string;
  ratingOverall: number | string | null;
  cover: ArtworkChoice | null;
}
