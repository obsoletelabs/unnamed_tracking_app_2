import { afterEach, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";
import { useMetadataArtwork } from "../composables/useMetadataArtwork";
import type { MetadataCandidate } from "../services/metadata";

const scopes: ReturnType<typeof effectScope>[] = [];
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop());
  vi.restoreAllMocks();
});
function setup(preview: (url: string) => Promise<Blob>) {
  const selected = ref<MetadataCandidate | null>({
    id: "one",
    title: "Portal",
    rank: 0,
    external_id: "42",
    provider: "core.steam",
    provider_name: "Steam",
    providers: ["core.steam"],
    provider_ids: { steam: "42" },
    media_type: "game",
    year: null,
    alternate_titles: [],
    metadata: {},
    assets: ["tall", "header"].map((url) => ({
      kind: "key_art" as const,
      url,
      width: null,
      height: null,
    })),
  });
  const scope = effectScope();
  scopes.push(scope);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:verified");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const artwork = scope.run(() => useMetadataArtwork(selected, preview))!;
  return { selected, artwork, scope };
}
const settle = async () => {
  for (let i = 0; i < 10; i++) await nextTick();
};

it("tries the host-verified Steam tall cover before the header and releases preview URLs", async () => {
  const preview = vi
    .fn()
    .mockRejectedValueOnce(new Error("404"))
    .mockResolvedValue(new Blob(["image"]));
  const { artwork, scope } = setup(preview);
  await settle();
  expect(preview.mock.calls.map((call) => call[0])).toEqual(["tall", "header"]);
  expect(artwork.urls.value.cover).toBe("header");
  expect(artwork.previews.value.header).toBe("blob:verified");
  scope.stop();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:verified");
});

it("ignores an artwork reply after cancellation without creating a stale blob URL", async () => {
  let finish!: (value: Blob) => void;
  const { selected, artwork } = setup(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  selected.value = null;
  await nextTick();
  finish(new Blob(["late"]));
  await settle();
  expect(artwork.urls.value).toEqual({});
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

it("ignores an older artwork-list revision of the same selected identity", async () => {
  let finish!: (value: Blob) => void;
  const { selected, artwork } = setup(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  selected.value = { ...selected.value!, assets: [] };
  await nextTick();
  finish(new Blob(["late"]));
  await settle();
  expect(artwork.urls.value.cover).toBeUndefined();
});
