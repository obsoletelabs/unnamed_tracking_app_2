import { onScopeDispose, ref, shallowRef, watch, type Ref } from "vue";
import type { MetadataCandidate } from "../services/metadata";

// Verify only the first few identity-bound choices; never probe providers in the browser.
export function useMetadataArtwork(
  selected: Ref<MetadataCandidate | null>,
  preview: (url: string) => Promise<Blob>,
) {
  const urls = shallowRef<{ cover?: string; banner?: string }>({});
  const previews = shallowRef<Record<string, string>>({});
  const busy = ref(false);
  let generation = 0;
  let revision = 0;
  let identity: string | undefined;
  const cache = new Map<string, Promise<string | null>>();
  function release() {
    Object.values(previews.value).forEach((url) => URL.revokeObjectURL(url));
    previews.value = {};
    cache.clear();
  }
  async function verify(url: string) {
    const request = generation;
    if (!cache.has(url))
      cache.set(
        url,
        preview(url)
          .then((blob) => {
            if (request !== generation) return null;
            const image = URL.createObjectURL(blob);
            previews.value = { ...previews.value, [url]: image };
            return url;
          })
          .catch(() => null),
      );
    return cache.get(url)!;
  }
  watch(
    () =>
      selected.value &&
      JSON.stringify([selected.value.id, selected.value.assets]),
    async () => {
      const work = ++revision;
      const candidate = selected.value;
      if (candidate?.id !== identity) {
        ++generation;
        identity = candidate?.id;
        release();
        urls.value = {};
      }
      const request = generation;
      if (!candidate) {
        busy.value = false;
        return;
      }
      busy.value = true;
      async function first(kinds: string[]) {
        const offered = candidate!.assets
          .filter((asset) => kinds.includes(asset.kind))
          .slice(0, 3);
        for (const asset of offered) {
          const url = await verify(asset.url);
          if (request !== generation) return undefined;
          if (url) return url;
        }
        return undefined;
      }
      const [cover, banner] = await Promise.all([
        first(["key_art", "poster"]),
        first(["banner", "hero"]),
      ]);
      if (request !== generation || work !== revision) return;
      urls.value = { cover, banner };
      busy.value = false;
    },
    { immediate: true },
  );
  onScopeDispose(() => {
    ++generation;
    release();
  });
  return { urls, previews, busy };
}
