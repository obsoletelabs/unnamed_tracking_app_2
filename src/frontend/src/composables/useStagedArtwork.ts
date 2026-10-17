import { computed, onScopeDispose, shallowRef } from "vue";

// One pending artwork choice for one role (cover, banner...). The most recent
// explicit choice wins: a local file stays staged until the person picks
// another file, a link, an offered image or "Keep current". Offered provider
// candidates never replace it on their own. Nothing is uploaded here; the
// editor's Save sends the staged choice.
export type StagedArtwork =
  | { source: "file"; file: File; preview: string }
  | { source: "link" | "provider"; url: string };

// A choice that can move between editors (the quick editor's "Edit more").
export type ArtworkChoice =
  { source: "file"; file: File } | { source: "link" | "provider"; url: string };

export function useStagedArtwork(
  current: () => string | null | undefined,
  initial?: ArtworkChoice | null,
) {
  const staged = shallowRef<StagedArtwork | null>(null);

  // Each local preview is an object URL; release it as soon as it is
  // replaced, cleared or the editor goes away.
  function release() {
    if (staged.value?.source === "file")
      URL.revokeObjectURL(staged.value.preview);
  }

  // A cancelled picker reports no file and keeps the existing choice.
  function chooseFile(file: File | null | undefined) {
    if (!file) return;
    release();
    staged.value = { source: "file", file, preview: URL.createObjectURL(file) };
  }

  function chooseUrl(
    url: string | null | undefined,
    source: "link" | "provider" = "provider",
  ) {
    const trimmed = url?.trim();
    release();
    staged.value = trimmed ? { source, url: trimmed } : null;
  }

  function reset() {
    release();
    staged.value = null;
  }

  // What the editor shows: the staged choice, otherwise the saved artwork.
  const preview = computed(() => {
    const choice = staged.value;
    if (!choice) return current() ?? null;
    return choice.source === "file" ? choice.preview : choice.url;
  });

  // The choice in a form that can seed another editor.
  function toChoice(): ArtworkChoice | null {
    const choice = staged.value;
    if (!choice) return null;
    return choice.source === "file"
      ? { source: "file", file: choice.file }
      : { source: choice.source, url: choice.url };
  }

  if (initial?.source === "file") chooseFile(initial.file);
  else if (initial) chooseUrl(initial.url, initial.source);

  onScopeDispose(release);

  return { staged, toChoice, preview, chooseFile, chooseUrl, reset };
}

// Stage a file from an <input type="file"> change event.
export function fileFromEvent(event: Event): File | null {
  return (event.target as HTMLInputElement | null)?.files?.[0] ?? null;
}

// Cleared before the picker opens, so choosing the same file again still
// fires `change` (the staged preview, not the native input, shows the name).
export function allowSameFileReselection(event: Event) {
  (event.currentTarget as HTMLInputElement).value = "";
}
