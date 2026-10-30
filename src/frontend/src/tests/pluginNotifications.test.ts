import { expect, it, vi } from "vitest";
import {
  registerPluginReminders,
  readPluginReminders,
} from "../state/pluginNotifications";

it("shows plugin-owned reminders and drops revoked results while isolating failures", async () => {
  let finish!: (
    items: Array<{
      id: string;
      label: string;
      description: string;
      path: string;
    }>,
  ) => void;
  const remove = registerPluginReminders(
    "example.archive",
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const bad = registerPluginReminders("example.failure", async () => {
    throw new Error("Offline");
  });
  const pending = readPluginReminders();
  remove();
  finish([
    {
      id: "due",
      label: "Quest",
      description: "Due today",
      path: "/plugins/example.archive/bounties",
    },
  ]);
  expect(await pending).toEqual([]);
  bad();
  const stop = registerPluginReminders("example.archive", async () => [
    {
      id: "due",
      label: "Quest",
      description: "Due today",
      path: "/plugins/example.archive/bounties?record_id=1",
    },
    {
      id: "escape",
      label: "Bad",
      description: "Bad",
      path: "/plugins/example.archive/../../settings",
    },
  ]);
  try {
    expect((await readPluginReminders()).map((item) => item.id)).toEqual([
      "due",
    ]);
  } finally {
    stop();
  }
});

it("shares pending reminder reads and reuses fresh results until their provider changes", async () => {
  let finish!: (
    value: Array<{
      id: string;
      label: string;
      description: string;
      path: string;
    }>,
  ) => void;
  const provider = vi.fn(
    () =>
      new Promise<
        Array<{ id: string; label: string; description: string; path: string }>
      >((resolve) => {
        finish = resolve;
      }),
  );
  const stop = registerPluginReminders("example.shared", provider);
  try {
    const one = readPluginReminders();
    const two = readPluginReminders();
    expect(provider).toHaveBeenCalledTimes(1);
    finish([
      {
        id: "one",
        label: "One",
        description: "Soon",
        path: "/plugins/example.shared/page",
      },
    ]);
    expect(await one).toEqual(await two);
    await readPluginReminders();
    expect(provider).toHaveBeenCalledTimes(1);
  } finally {
    stop();
  }
});
