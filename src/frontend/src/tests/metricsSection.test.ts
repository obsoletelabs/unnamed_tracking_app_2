// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import MetricsSection from "../components/settings/MetricsSection.vue";
const state = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("../services/metrics", () => ({ fetchMetricsSnapshot: state.fetch }));
vi.mock("../state/auth", async () => {
  const { ref } = await import("vue");
  return { currentUser: ref({ id: "admin", is_admin: true }) };
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  state.fetch.mockReset();
});
it("polls only while open, visible and unpaused, and stops after unmount", async () => {
  vi.useFakeTimers();
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("visible");
  state.fetch.mockResolvedValue({
    enabled: true,
    started_at: 1,
    collected_at: 1,
    histograms: { http: [] },
    values: {},
  });
  const wrapper = mount(MetricsSection);
  try {
    await flushPromises();
    expect(wrapper.text()).not.toContain(String.fromCharCode(0xfffd));
    expect(state.fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(state.fetch).toHaveBeenCalledTimes(2);
    await wrapper.get("button").trigger("click");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(state.fetch).toHaveBeenCalledTimes(2);
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(state.fetch).toHaveBeenCalledTimes(3);
    visibility.mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(20_000);
    expect(state.fetch).toHaveBeenCalledTimes(3);
  } finally {
    wrapper.unmount();
  }
  await vi.advanceTimersByTimeAsync(20_000);
  expect(state.fetch).toHaveBeenCalledTimes(3);
});
it("shows disabled metrics without leaving a polling loop", async () => {
  vi.useFakeTimers();
  state.fetch.mockResolvedValue({ enabled: false });
  const wrapper = mount(MetricsSection);
  try {
    await flushPromises();
    expect(wrapper.text()).toContain("Metrics are disabled");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(state.fetch).toHaveBeenCalledTimes(1);
  } finally {
    wrapper.unmount();
  }
});
