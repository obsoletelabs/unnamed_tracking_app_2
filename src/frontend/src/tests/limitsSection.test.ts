// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LimitsSection from "../components/settings/LimitsSection.vue";
import type { UploadLimitDetails } from "../services/settings";
import {
  fetchUploadLimitDetails,
  updateUploadLimits,
} from "../services/settings";

vi.mock("../services/settings", () => ({
  fetchUploadLimitDetails: vi.fn(),
  updateUploadLimits: vi.fn(),
}));

function details(locked: Partial<Record<string, number>> = {}) {
  const base = {
    max_upload_size_mb: 15,
    max_doc_size_mb: 500,
    max_save_archive_size_mb: 4096,
    max_clip_size_mb: 500,
    max_world_save_size_mb: 2000,
  };
  return Object.fromEntries(
    Object.entries(base).map(([key, value]) => [
      key,
      {
        value: locked[key] ?? value,
        source: key in locked ? "environment" : "default",
        locked: key in locked,
        env_name: key.toUpperCase(),
        environment_invalid: false,
      },
    ]),
  ) as UploadLimitDetails;
}

describe("LimitsSection", () => {
  beforeEach(() => {
    vi.mocked(fetchUploadLimitDetails).mockReset();
    vi.mocked(updateUploadLimits).mockReset();
  });

  it("locks environment-controlled caps, explains why and never submits them", async () => {
    vi.mocked(fetchUploadLimitDetails).mockResolvedValue(
      details({ max_doc_size_mb: 77 }),
    );
    vi.mocked(updateUploadLimits).mockResolvedValue({} as never);
    const wrapper = mount(LimitsSection);
    await flushPromises();

    const inputs = wrapper.findAll("input");
    expect(inputs).toHaveLength(5);
    const locked = inputs.filter(
      (input) => input.attributes("disabled") === "",
    );
    expect(locked).toHaveLength(1);
    expect((locked[0].element as HTMLInputElement).value).toBe("77");
    expect(wrapper.text()).toContain(
      "Set by the MAX_DOC_SIZE_MB environment variable",
    );
    expect(wrapper.text()).toContain("reverse proxy");

    await wrapper.find("form").trigger("submit");
    await flushPromises();
    const sent = vi.mocked(updateUploadLimits).mock.calls[0][0];
    expect(sent).not.toHaveProperty("max_doc_size_mb");
    expect(sent).toHaveProperty("max_upload_size_mb", 15);
  });

  it("shows the server's effective values after a rejected save", async () => {
    vi.mocked(fetchUploadLimitDetails).mockResolvedValue(details());
    vi.mocked(updateUploadLimits).mockRejectedValue(
      new Error("Failed to update upload limits: 409 managed"),
    );
    const wrapper = mount(LimitsSection);
    await flushPromises();
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find("[role=alert]").text()).toContain("409");
  });
});
