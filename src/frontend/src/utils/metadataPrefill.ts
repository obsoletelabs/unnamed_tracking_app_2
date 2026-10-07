// A late enrichment update may replace an earlier automatic value, but never a manual edit.
export function metadataPrefill<T extends object>() {
  const previous = new Map<keyof T, unknown>();
  let candidateId: string | undefined;
  return (fields: T, incoming: Partial<T>, identity: string | undefined) => {
    if (identity !== candidateId) {
      previous.clear();
      candidateId = identity;
    }
    for (const key of Object.keys(incoming) as (keyof T)[]) {
      const value = incoming[key];
      if (
        value == null ||
        value === "" ||
        (Array.isArray(value) && !value.length)
      )
        continue;
      if (previous.has(key) && fields[key] !== previous.get(key)) continue;
      Object.assign(fields, { [key]: value });
      previous.set(key, value);
    }
  };
}
