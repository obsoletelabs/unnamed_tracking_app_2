// For the pages App.vue keeps alive when you navigate away (the three
// libraries, Calendar, Lists). Their state and DOM survive, so swapping
// tabs preserves the page's loaded data instead of remounting and
// flashing empty ("0 titles") while the data loads again. Two things
// still need doing by hand: pull fresh data in the background each time
// the page comes back. The router owns scrolling: fresh visits start at the
// top and browser Back/Forward can restore their history position.
import { onActivated, onDeactivated, onMounted, onUnmounted, watch } from "vue";

export function useKeptAlive(
  refresh: () => void,
  recovery?: { isLoading: () => boolean; hasError: () => boolean },
): void {
  let firstActivation = true;
  let active = true;
  let retryPending = false;
  onActivated(() => {
    active = true;
    if (firstActivation) {
      // the first activation is just the initial mount, which already loads
      firstActivation = false;
      return;
    }
    retryPending = false;
    refresh();
  });

  // Recovery is opt-in: unrelated kept-alive pages retain their existing
  // activation behavior. Only the visible library makes network requests.
  if (!recovery) return;
  const { isLoading, hasError } = recovery;
  function retry() {
    if (
      !active ||
      document.visibilityState === "hidden" ||
      navigator.onLine === false ||
      isLoading() ||
      (!retryPending && !hasError())
    )
      return;
    retryPending = false;
    refresh();
  }
  function online() {
    retryPending = true;
    retry();
  }
  function offline() {
    retryPending = true;
  }
  onMounted(() => {
    retryPending = navigator.onLine === false;
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    window.addEventListener("focus", retry);
    document.addEventListener("visibilitychange", retry);
  });
  onDeactivated(() => {
    active = false;
  });
  onUnmounted(() => {
    active = false;
    window.removeEventListener("online", online);
    window.removeEventListener("offline", offline);
    window.removeEventListener("focus", retry);
    document.removeEventListener("visibilitychange", retry);
  });
  watch(isLoading, (loading) => {
    // A reconnect during an older request must wait for it to settle. Clearing
    // the pending flag before starting the retry avoids a polling loop.
    if (!loading && retryPending) retry();
  });
}
