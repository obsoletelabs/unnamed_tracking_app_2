import { computed, ref, shallowRef, watch } from "vue";

export interface MetadataField {
  label: string;
  locked?: boolean;
  image?: boolean;
}

export interface MetadataChange {
  key: string;
  label: string;
  current: unknown;
  offered: unknown;
  checked: boolean;
  locked: boolean;
  image: boolean;
}

export function metadataEmpty(value: unknown): boolean {
  return (
    value == null ||
    (typeof value === "string" && !value.trim()) ||
    (Array.isArray(value) && !value.length) ||
    (typeof value === "object" &&
      !Array.isArray(value) &&
      Object.values(value).every(metadataEmpty))
  );
}

const snapshot = (value: unknown) => JSON.stringify(value);
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value));

// The comparison is form-side only: the caller's existing Save owns persistence.
export function useMetadataComparison<T extends object>(
  current: () => T,
  definitions: () => Partial<Record<keyof T, MetadataField>>,
) {
  const offered = shallowRef<Partial<T>>({});
  const choices = shallowRef(
    new Map<keyof T, { checked: boolean; from: string | undefined }>(),
  );
  let identity = "";
  let applying = false;
  const reason = ref<string | null>(null);
  watch(
    () => {
      const fields = current();
      return "title" in fields ? fields.title : undefined;
    },
    () => {
      if (applying || !identity) return;
      offered.value = {};
      choices.value = new Map();
      reason.value =
        "The form title changed. Search again to review metadata for the new title.";
    },
    { flush: "sync" },
  );

  const changes = computed<MetadataChange[]>(() => {
    const fields = current();
    return (Object.keys(offered.value) as (keyof T)[]).flatMap((key) => {
      const definition = definitions()[key];
      const value = offered.value[key];
      if (
        !definition ||
        metadataEmpty(value) ||
        snapshot(value) === snapshot(fields[key])
      )
        return [];
      const choice = choices.value.get(key);
      return [
        {
          key: String(key),
          label: definition.label,
          current: fields[key],
          offered: value,
          checked:
            !definition.locked &&
            (choice
              ? choice.checked && choice.from === snapshot(fields[key])
              : metadataEmpty(fields[key])),
          locked: !!definition.locked,
          image: !!definition.image,
        },
      ];
    });
  });

  function offer(values: Partial<T>, candidateIdentity: string) {
    if (candidateIdentity === identity && reason.value) return;
    if (candidateIdentity !== identity) {
      choices.value = new Map();
      reason.value = null;
    }
    identity = candidateIdentity;
    const next = new Map(choices.value);
    for (const key of Object.keys(values) as (keyof T)[]) {
      if (!next.has(key) && !metadataEmpty(values[key]))
        next.set(key, {
          checked: metadataEmpty(current()[key]),
          from: snapshot(current()[key]),
        });
    }
    choices.value = next;
    // Copy provider values so later streamed mutations cannot alter this preview.
    offered.value = copy(values);
  }

  function toggle(key: string, checked: boolean) {
    const field = key as keyof T;
    choices.value = new Map(choices.value).set(field, {
      checked,
      from: snapshot(current()[field]),
    });
  }

  function clear() {
    offered.value = {};
    choices.value = new Map();
    identity = "";
    reason.value = null;
  }

  function apply(): Set<keyof T> {
    const selected = changes.value.filter(
      (change) => change.checked && !change.locked,
    );
    const fields = current();
    const keys = new Set<keyof T>();
    applying = true;
    try {
      for (const change of selected) {
        const key = change.key as keyof T;
        Object.assign(fields, { [key]: copy(offered.value[key]) });
        keys.add(key);
      }
    } finally {
      applying = false;
      clear();
    }
    return keys;
  }

  return { changes, offer, toggle, clear, apply, reason };
}
