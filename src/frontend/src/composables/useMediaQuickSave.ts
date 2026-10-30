import { ref, watch, type Ref } from "vue";

/**
 * One-click changes (favourite, note, status, rating) on a movie, TV show or
 * anime page. Each save sends the whole record, so saves for a title are sent
 * one at a time, each as the last record the server accepted plus that one
 * change. The change shows at once; when the queue is done the page shows the
 * server's record, so a failed change is undone without undoing the others.
 * Answers for a title the page has left are kept off the page.
 */
export function useMediaQuickSave<T extends { id: string }>(
  record: Ref<T | null>,
  persist: (body: T) => Promise<T>,
) {
  const saveError = ref<string | null>(null);
  watch(
    () => record.value?.id,
    () => (saveError.value = null),
  );
  const queues = new Map<
    string,
    { tail: Promise<void>; confirmed: T; pending: number }
  >();

  async function quickSave(
    change: (target: T) => void,
    failure: string,
  ): Promise<boolean> {
    const target = record.value;
    if (!target) return false;
    const id = target.id;
    let queue = queues.get(id);
    if (!queue) {
      queue = { tail: Promise.resolve(), confirmed: { ...target }, pending: 0 };
      queues.set(id, queue);
    }
    const current = queue;
    change(target);
    current.pending += 1;
    saveError.value = null;
    let saved = false;
    current.tail = current.tail.then(async () => {
      const body = { ...current.confirmed };
      change(body);
      try {
        current.confirmed = await persist(body);
        saved = true;
      } catch {
        if (record.value?.id === id) saveError.value = failure;
      }
    });
    await current.tail;
    current.pending -= 1;
    if (current.pending === 0) {
      queues.delete(id);
      if (record.value?.id === id) record.value = current.confirmed;
    }
    return saved;
  }

  return { saveError, quickSave };
}
