/**
 * Optimistic edits that are saved one at a time per field and record.
 *
 * The new value shows at once. Saves for the same record queue behind each
 * other, so two quick changes reach the server in the order they were made.
 * When the newest save fails, the field returns to the last value the server
 * accepted, on the record it was made for (never on a record opened since).
 */
export function orderedFieldSave<Target extends object, Value>(options: {
  read: (target: Target) => Value;
  write: (target: Target, value: Value) => void;
  save: (target: Target, value: Value) => Promise<unknown>;
  onFailure: (target: Target, error: unknown) => void;
}): (target: Target, value: Value) => Promise<boolean> {
  const queues = new WeakMap<
    Target,
    { tail: Promise<void>; confirmed: Value; pending: number; version: number }
  >();
  return async (target, value) => {
    let queue = queues.get(target);
    if (!queue) {
      queue = {
        tail: Promise.resolve(),
        confirmed: options.read(target),
        pending: 0,
        version: 0,
      };
      queues.set(target, queue);
    }
    const current = queue;
    let saved = false;
    options.write(target, value);
    current.pending += 1;
    const version = ++current.version;
    current.tail = current.tail.then(async () => {
      try {
        await options.save(target, value);
        current.confirmed = value;
        saved = true;
      } catch (error) {
        if (version === current.version) options.onFailure(target, error);
      }
    });
    await current.tail;
    current.pending -= 1;
    if (current.pending === 0) {
      queues.delete(target);
      options.write(target, current.confirmed);
    }
    return saved;
  };
}
