// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TasksSection from "../components/settings/TasksSection.vue";
import type { CleanupJob } from "../services/settings";
import { fetchJobs, runJobNow, updateJob } from "../services/settings";

vi.mock("../services/settings", () => ({
  fetchJobs: vi.fn(),
  updateJob: vi.fn(),
  runJobNow: vi.fn(),
  controlJob: vi.fn(),
}));

function makeJob(overrides: Partial<CleanupJob> = {}): CleanupJob {
  return {
    id: "cleanup",
    name: "Trash cleanup",
    description: "Removes old trash",
    enabled: false,
    intervalMinutes: 360,
    minIntervalMinutes: 15,
    maxIntervalMinutes: 10080,
    lastRunAt: null,
    lastSummary: "",
    lastResult: {},
    running: false,
    pluginId: null,
    pluginName: null,
    available: true,
    unavailableReason: "",
    paused: false,
    status: "idle",
    lastSuccessAt: null,
    nextRunAt: null,
    failureCount: 0,
    ...overrides,
  };
}

const fetchMock = vi.mocked(fetchJobs);
const updateMock = vi.mocked(updateJob);
const runMock = vi.mocked(runJobNow);

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

async function mountWith(jobs: CleanupJob[]) {
  fetchMock.mockResolvedValue(jobs);
  const wrapper = mount(TasksSection);
  await flushPromises();
  return wrapper;
}

function tile(wrapper: Awaited<ReturnType<typeof mountWith>>, index = 0) {
  return wrapper.findAll(".tile")[index];
}

describe("TasksSection interval drafts", () => {
  it("enters 90 minutes from an hours value without an intermediate save or error", async () => {
    const wrapper = await mountWith([makeJob()]);
    const t = tile(wrapper);
    expect((t.get(".job-amount").element as HTMLInputElement).value).toBe("6");
    await t.get(".job-interval").setValue("minutes");
    await t.get(".job-amount").setValue("90");
    expect(updateMock).not.toHaveBeenCalled();
    expect(t.find("[role='alert']").exists()).toBe(false);
    expect(t.text()).toContain("Unsaved change.");

    updateMock.mockResolvedValue(makeJob({ intervalMinutes: 90 }));
    await t.get(".interval-save").trigger("click");
    await flushPromises();
    expect(updateMock).toHaveBeenCalledTimes(1);
    expect(updateMock).toHaveBeenCalledWith(
      "cleanup",
      { intervalMinutes: 90 },
      false,
    );
    expect((t.get(".job-amount").element as HTMLInputElement).value).toBe("90");
    expect(t.find(".interval-reset").exists()).toBe(false);
  });

  it("validates the combined draft and keeps it after a server failure", async () => {
    const wrapper = await mountWith([makeJob()]);
    const t = tile(wrapper);
    await t.get(".job-interval").setValue("minutes");
    await t.get(".job-amount").setValue("5");
    await t.get(".interval-save").trigger("click");
    expect(updateMock).not.toHaveBeenCalled();
    expect(t.get("[role='alert']").text()).toContain(
      "Choose between 15 minutes and 1 week.",
    );

    await t.get(".job-amount").setValue("90");
    updateMock.mockRejectedValue(new Error("Server said no"));
    await t.get(".interval-save").trigger("click");
    await flushPromises();
    expect(t.get("[role='alert']").text()).toContain("Server said no");
    expect((t.get(".job-amount").element as HTMLInputElement).value).toBe("90");
    expect((t.get(".job-interval").element as HTMLSelectElement).value).toBe(
      "minutes",
    );
  });

  it("restores the saved value on reset", async () => {
    const wrapper = await mountWith([makeJob()]);
    const t = tile(wrapper);
    await t.get(".job-interval").setValue("days");
    await t.get(".job-amount").setValue("2");
    await t.get(".interval-reset").trigger("click");
    expect((t.get(".job-amount").element as HTMLInputElement).value).toBe("6");
    expect((t.get(".job-interval").element as HTMLSelectElement).value).toBe(
      "hours",
    );
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("keeps edits per task, leaves scheduling off and survives polling", async () => {
    const wrapper = await mountWith([
      makeJob(),
      makeJob({ id: "other", name: "Other", intervalMinutes: 60 }),
    ]);
    await tile(wrapper).get(".job-amount").setValue("3");
    expect(
      (tile(wrapper).get(".job-toggle input").element as HTMLInputElement)
        .checked,
    ).toBe(false);
    expect(
      (tile(wrapper, 1).get(".job-amount").element as HTMLInputElement).value,
    ).toBe("1");
    expect(
      tile(wrapper, 1).get(".interval-save").attributes("disabled"),
    ).toBeDefined();

    fetchMock.mockResolvedValue([
      makeJob({ running: true }),
      makeJob({ id: "other", name: "Other", intervalMinutes: 120 }),
    ]);
    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();
    expect(
      (tile(wrapper).get(".job-amount").element as HTMLInputElement).value,
    ).toBe("3");
    expect(
      (tile(wrapper, 1).get(".job-amount").element as HTMLInputElement).value,
    ).toBe("2");
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("offers only units that fit the allowed range", async () => {
    const wrapper = await mountWith([
      makeJob({
        minIntervalMinutes: 5,
        maxIntervalMinutes: 1440,
        intervalMinutes: 60,
      }),
    ]);
    const options = tile(wrapper)
      .findAll(".job-interval option")
      .map((o) => o.text());
    expect(options).toEqual(["minutes", "hours", "days"]);
    expect(tile(wrapper).text()).toContain("Allowed: 5 minutes to 1 day.");
  });

  it("runs now while automatic scheduling is off", async () => {
    const wrapper = await mountWith([makeJob({ enabled: false })]);
    runMock.mockResolvedValue(undefined);
    const button = tile(wrapper)
      .findAll("button")
      .find((b) => b.text() === "Run now");
    expect(button?.attributes("disabled")).toBeUndefined();
    await button?.trigger("click");
    await flushPromises();
    expect(runMock).toHaveBeenCalledWith("cleanup", false);
    expect(updateMock).not.toHaveBeenCalled();
  });
});
