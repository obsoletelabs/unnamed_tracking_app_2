/**
 * "Only the newest request may write": call `start()` before a request and
 * check `isLatest(token)` after each await. `cancel()` invalidates whatever is
 * in flight, for example when the page moves to another title.
 */
export function latestRequest() {
  let current = 0;
  return {
    start: (): number => ++current,
    isLatest: (token: number): boolean => token === current,
    cancel: (): void => {
      current++;
    },
  };
}
