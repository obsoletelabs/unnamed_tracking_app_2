// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import TasksSection from "../components/settings/TasksSection.vue";
import {
  fetchJobs,
  runJobNow,
  updateJob,
  type CleanupJob,
} from "../services/settings";

vi.mock("../services/settings", async (original) => ({
  ...(await original<typeof import("../services/settings")>()),
  fetchJobs: vi.fn(),
  updateJob: vi.fn(),
  runJobNow: vi.fn().mockResolvedValue(undefined),
  controlJob: vi.fn(),
}));

function job(id: string, extra: Partial<CleanupJob> = {}): CleanupJob {
  return {
    id,
    name: `Task ${id}`,
    description: "",
    enabled: false,
    intervalMinutes: 1440,
    minIntervalMinutes: 15,
    maxIntervalMinutes: 60 * 24 * 90,
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
    ...extra,
  };
}

beforeEach(() => {
  vi.mocked(fetchJobs).mockResolvedValue([job("a"), job("b")]);
});
afterEach(() => vi.clearAllMocks());

async function setup() {
  const wrapper = mount(TasksSection);
  await flushPromises();
  return wrapper;
}
const amount = (w: ReturnType<typeof mount>, i: number) =>
  w.findAll("input.job-amount")[i];
const unit = (w: ReturnType<typeof mount>, i: number) =>
  w.findAll("select.job-interval")[i];
const saveButton = (w: ReturnType<typeof mount>, i: number) =>
  w.findAll("button").filter((b) => b.text().includes("Save schedule"))[i];

it("lets a schedule be edited as one draft and saves only on Save (#252)", async () => {
  const wrapper = await setup();
  // 1 day is shown as 1 day; Save is disabled until something differs
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("1");
  expect((unit(wrapper, 0).element as HTMLSelectElement).value).toBe("days");
  expect(saveButton(wrapper, 0).attributes("disabled")).toBeDefined();

  // switching to minutes keeps the same length of time instead of jumping to 1 minute
  await unit(wrapper, 0).setValue("minutes");
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("1440");
  // the same length of time is not a change, so there is nothing to save yet
  expect(saveButton(wrapper, 0).attributes("disabled")).toBeDefined();
  // typing 90 minutes passes through no blocking error and nothing was saved yet
  await amount(wrapper, 0).setValue("90");
  expect(saveButton(wrapper, 0).attributes("disabled")).toBeUndefined();
  expect(updateJob).not.toHaveBeenCalled();
  expect(wrapper.find(".interval-help.invalid").exists()).toBe(false);

  vi.mocked(updateJob).mockResolvedValue(job("a", { intervalMinutes: 90 }));
  await saveButton(wrapper, 0).trigger("click");
  await flushPromises();
  expect(updateJob).toHaveBeenCalledTimes(1);
  expect(vi.mocked(updateJob).mock.calls[0].slice(0, 2)).toEqual([
    "a",
    { intervalMinutes: 90 },
  ]);
  // the saved 90 minutes is shown back as 90 minutes
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("90");
  expect((unit(wrapper, 0).element as HTMLSelectElement).value).toBe("minutes");
});

it("validates the combined draft on Save and keeps it when the server refuses", async () => {
  const wrapper = await setup();
  await unit(wrapper, 0).setValue("minutes");
  await amount(wrapper, 0).setValue("5"); // below the 15 minute floor
  await saveButton(wrapper, 0).trigger("click");
  expect(updateJob).not.toHaveBeenCalled();
  expect(wrapper.find(".interval-help.invalid").text()).toContain(
    "Choose between",
  );

  await amount(wrapper, 0).setValue("30");
  vi.mocked(updateJob).mockRejectedValue(new Error("Server says no"));
  await saveButton(wrapper, 0).trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Server says no");
  // the draft survives the failure and can be retried or cancelled
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("30");
  const cancel = wrapper.findAll("button").find((b) => b.text() === "Cancel")!;
  await cancel.trigger("click");
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("1");
  expect((unit(wrapper, 0).element as HTMLSelectElement).value).toBe("days");
});

it("never enables a task or touches another task while a draft is edited", async () => {
  const wrapper = await setup();
  await unit(wrapper, 0).setValue("hours");
  await amount(wrapper, 0).setValue("3");
  expect(updateJob).not.toHaveBeenCalled();
  expect((amount(wrapper, 1).element as HTMLInputElement).value).toBe("1");
  vi.mocked(updateJob).mockResolvedValue(job("a", { intervalMinutes: 180 }));
  await saveButton(wrapper, 0).trigger("click");
  await flushPromises();
  expect(vi.mocked(updateJob).mock.calls[0][1]).toEqual({
    intervalMinutes: 180,
  });
  const checkboxes = wrapper.findAll('input[type="checkbox"]');
  expect(
    checkboxes.every((box) => !(box.element as HTMLInputElement).checked),
  ).toBe(true);
});

it("keeps unsaved edits through a refresh but lets clean fields follow the server", async () => {
  vi.useFakeTimers();
  const wrapper = await setup();
  await amount(wrapper, 0).setValue("7");
  vi.mocked(fetchJobs).mockResolvedValue([
    job("a"),
    job("b", { intervalMinutes: 120 }), // changed elsewhere
  ]);
  await vi.advanceTimersByTimeAsync(5100);
  await flushPromises();
  expect((amount(wrapper, 0).element as HTMLInputElement).value).toBe("7");
  expect((amount(wrapper, 1).element as HTMLInputElement).value).toBe("2");
  expect((unit(wrapper, 1).element as HTMLSelectElement).value).toBe("hours");
  vi.useRealTimers();
});

it("still runs a task now whether or not it runs by itself", async () => {
  const wrapper = await setup();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "Run now")!
    .trigger("click");
  await flushPromises();
  expect(runJobNow).toHaveBeenCalledWith("a", false);
});
