// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import LimitsSection from "../components/settings/LimitsSection.vue";
import {
  fetchUploadLimitSources,
  updateUploadLimits,
  type UploadLimitSources,
} from "../services/settings";

vi.mock("../services/settings", async (original) => ({
  ...(await original<typeof import("../services/settings")>()),
  fetchUploadLimitSources: vi.fn(),
  updateUploadLimits: vi.fn().mockResolvedValue({}),
}));

function sources(): UploadLimitSources {
  const make = (
    key: string,
    value: number,
    source: "environment" | "override" | "default",
    extra = {},
  ) => ({
    value,
    source,
    locked: source === "environment",
    environment_variable: key.toUpperCase(),
    environment_value: source === "environment" ? value : null,
    environment_invalid: false,
    stored_override: null,
    ...extra,
  });
  return {
    max_upload_size_mb: make("max_upload_size_mb", 15, "default"),
    max_doc_size_mb: make("max_doc_size_mb", 64, "environment"),
    max_save_archive_size_mb: make(
      "max_save_archive_size_mb",
      4096,
      "override",
    ),
    max_clip_size_mb: make("max_clip_size_mb", 500, "default", {
      environment_invalid: true,
    }),
    max_world_save_size_mb: make("max_world_save_size_mb", 2000, "default"),
  };
}

afterEach(() => vi.clearAllMocks());

it("shows where each cap comes from and locks the ones the environment sets (#253)", async () => {
  vi.mocked(fetchUploadLimitSources).mockResolvedValue(sources());
  const wrapper = mount(LimitsSection);
  await flushPromises();
  const inputs = wrapper.findAll("input");
  expect(inputs.map((i) => i.attributes("disabled") !== undefined)).toEqual([
    false,
    true,
    false,
    false,
    false,
  ]);
  const text = wrapper.text();
  expect(text).toContain("Set by the server environment (MAX_DOC_SIZE_MB)");
  expect(text).toContain("Saved here, overriding the environment default.");
  expect(text).toContain("MAX_CLIP_SIZE_MB is set but is not a whole number");
  expect(text).toContain("client_max_body_size");
});

it("saves only the editable caps and never sends a locked one", async () => {
  vi.mocked(fetchUploadLimitSources).mockResolvedValue(sources());
  const wrapper = mount(LimitsSection);
  await flushPromises();
  await wrapper.findAll("input")[0].setValue("20");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  const sent = vi.mocked(updateUploadLimits).mock.calls[0][0];
  expect(sent).toEqual({
    max_upload_size_mb: 20,
    max_save_archive_size_mb: 4096,
    max_clip_size_mb: 500,
    max_world_save_size_mb: 2000,
  });
  expect(sent).not.toHaveProperty("max_doc_size_mb");
});
