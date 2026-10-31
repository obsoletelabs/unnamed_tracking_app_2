import { ref, watch, type Ref } from "vue";

/**
 * Serialize quick edits per title, sending only the changed fields. Independent
 * editors may replace the record while a request is pending; neither an older
 * response nor a rollback may replace their unrelated confirmed changes.
 */
export function useMediaQuickSave<T extends { id: string }>(
  record: Ref<T | null>,
  persist: (id: string, changes: Partial<T>) => Promise<T>,
) {
  const saveError = ref<string | null>(null);
  const queues = new Map<
    string,
    {
      tail: Promise<void>;
      confirmed: T;
      pending: number;
      revision: number;
      fields: Set<keyof T>;
      errors: Map<keyof T, string>;
    }
  >();
  watch(
    record,
    (value, previous) => {
      if (value?.id !== previous?.id) saveError.value = null;
      const queue = value && queues.get(value.id);
      if (queue && value !== previous) {
        queue.confirmed = { ...value };
        queue.revision += 1;
      }
    },
    { flush: "sync" },
  );

  async function quickSave(
    change: (target: T) => void,
    failure: string,
  ): Promise<boolean> {
    const target = record.value;
    if (!target) return false;
    const id = target.id;
    let queue = queues.get(id);
    if (!queue) {
      queue = {
        tail: Promise.resolve(),
        confirmed: { ...target },
        pending: 0,
        revision: 0,
        fields: new Set(),
        errors: new Map(),
      };
      queues.set(id, queue);
    }
    const current = queue;
    const next = { ...target };
    change(next);
    const fields = (Object.keys(next) as (keyof T)[]).filter(
      (key) => !Object.is(next[key], target[key]),
    );
    const changes: Partial<T> = {};
    for (const key of fields) {
      changes[key] = next[key];
      target[key] = next[key];
      current.fields.add(key);
    }
    current.pending += 1;
    saveError.value = null;
    let saved = false;
    current.tail = current.tail.then(async () => {
      const revision = current.revision;
      try {
        const result = await persist(id, changes);
        if (revision === current.revision) {
          for (const key of fields) current.confirmed[key] = result[key];
        }
        for (const key of fields) current.errors.delete(key);
        saved = true;
      } catch {
        if (revision === current.revision) {
          for (const key of fields) current.errors.set(key, failure);
        }
      }
    });
    await current.tail;
    current.pending -= 1;
    if (current.pending === 0) {
      queues.delete(id);
      if (record.value?.id === id) {
        for (const key of current.fields)
          record.value[key] = current.confirmed[key];
        saveError.value = [...current.errors.values()].at(-1) ?? null;
      }
    }
    return saved;
  }

  return { saveError, quickSave };
}
