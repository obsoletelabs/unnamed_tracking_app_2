import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchJobs, updateJob, runJobNow } from "../services/settings";

afterEach(() => vi.unstubAllGlobals());
const job = {
  id: "user-plugin:fixture",
  name: "Library autosync",
  description: "Personal sync",
  enabled: false,
  interval_minutes: 1440,
  min_interval_minutes: 5,
  max_interval_minutes: 10080,
  last_run_at: null,
  last_summary: "",
  last_result: {},
  running: false,
  available: true,
};

describe("personal background task transport", () => {
  it("lists personal jobs independently from administrator maintenance jobs", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify([job])));
    vi.stubGlobal("fetch", fetcher);
    expect((await fetchJobs(true))[0].enabled).toBe(false);
    expect(fetcher).toHaveBeenCalledWith("/api/settings/user-jobs", {
      credentials: "include",
    });
  });

  it("writes the personal schedule through its authenticated user endpoint", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ...job, enabled: true })),
      );
    vi.stubGlobal("fetch", fetcher);
    expect(
      (await updateJob(job.id, { enabled: true, intervalMinutes: 60 }, true))
        .enabled,
    ).toBe(true);
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe(`/api/settings/user-jobs/${job.id}`);
    expect(JSON.parse(options.body)).toEqual({
      enabled: true,
      interval_minutes: 60,
    });
  });

  it("runs a personal job and preserves live permission denials", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: "Background permission is unavailable." }),
          { status: 409 },
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    await expect(runJobNow(job.id, true)).rejects.toThrow(
      "Background permission is unavailable.",
    );
    expect(fetcher.mock.calls[0][0]).toBe(
      `/api/settings/user-jobs/${job.id}/run`,
    );
  });
});
