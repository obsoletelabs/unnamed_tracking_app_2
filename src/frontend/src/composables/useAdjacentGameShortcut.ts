import { onUnmounted, type Ref } from "vue";
import type { Router } from "vue-router";
import { peekAdjacentGameId } from "../state/libraryNav";
import { matchesShortcut } from "../state/shortcuts";
import type { Game } from "../types/game";

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  );
}

/** J/K next/prev game on the game page, mirroring the library grid's own shortcut. */
export function useAdjacentGameShortcut(
  game: Ref<Game | null>,
  router: Router,
  blocked: () => boolean,
) {
  function onDetailKeydown(e: KeyboardEvent) {
    if (
      e.defaultPrevented ||
      e.isComposing ||
      e.repeat ||
      e.getModifierState("AltGraph") ||
      document.querySelector("dialog[open]")
    )
      return;
    if (isTypingTarget(e.target)) return;
    if (blocked() || !game.value) return;
    const next = matchesShortcut("games.next", e);
    if (next || matchesShortcut("games.previous", e)) {
      const nextId = peekAdjacentGameId(game.value.id, next ? 1 : -1);
      if (nextId) {
        e.preventDefault();
        router.push(`/games/${nextId}`);
      }
    }
  }
  window.addEventListener("keydown", onDetailKeydown);
  onUnmounted(() => window.removeEventListener("keydown", onDetailKeydown));
}
