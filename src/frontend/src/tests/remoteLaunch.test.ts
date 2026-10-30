// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import GameLaunchButton from "../components/game/GameLaunchButton.vue";
import type { Game } from "../types/game";
import {
  fetchLaunchDevices,
  fetchRemoteLaunch,
  queueRemoteLaunch,
  type RemoteLaunchRequest,
} from "../services/remoteLaunch";

vi.mock("../services/remoteLaunch", async (original) => ({
  ...(await original<typeof import("../services/remoteLaunch")>()),
  fetchLaunchDevices: vi.fn(),
  fetchRemoteLaunch: vi.fn(),
  queueRemoteLaunch: vi.fn(),
}));
vi.mock("../services/gameIdentity", () => ({
  fetchIdentitySources: vi.fn().mockResolvedValue({ observations: [] }),
}));
let wrapper: VueWrapper | undefined;
const guid = "d557cd32-3342-41d1-bab3-32c01ef86aa8";
const device = {
  id: "pc-1",
  name: "Gaming PC",
  online: true,
  last_seen_at: 100,
};
const result = (
  state: RemoteLaunchRequest["state"] = "pending",
): RemoteLaunchRequest => ({
  id: "request-1",
  device_id: device.id,
  game_id: "game-1",
  state,
  failure_code: null,
  created_at: Date.now() / 1000,
  expires_at: Date.now() / 1000 + 60,
});
function game(status = "backlog"): Game {
  return {
    id: "game-1",
    playniteGuid: guid,
    providerIds: {},
    status,
  } as unknown as Game;
}
async function openMenu(status = "backlog") {
  wrapper = mount(GameLaunchButton, {
    props: { game: game(status) },
    attachTo: document.body,
  });
  await flushPromises();
  await wrapper.get('[aria-label="More ways to launch"]').trigger("click");
  await flushPromises();
}
function button(text: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll("button")).find(
    (node) =>
      (node.getAttribute("aria-label") ?? node.textContent?.trim()) === text,
  );
  expect(found, text).toBeTruthy();
  return found!;
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(fetchLaunchDevices).mockResolvedValue([device]);
  vi.mocked(queueRemoteLaunch).mockResolvedValue(result());
  vi.mocked(fetchRemoteLaunch).mockResolvedValue(result("accepted"));
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  vi.useRealTimers();
  vi.clearAllMocks();
  document.body.innerHTML = "";
});

it("offers remote launch for a Playnite-only game and tracks its result", async () => {
  await openMenu();
  button("Remote start on Gaming PC").click();
  await flushPromises();
  expect(queueRemoteLaunch).toHaveBeenCalledOnce();
  expect(vi.mocked(queueRemoteLaunch).mock.calls[0]![0]).toMatchObject({
    game_id: "game-1",
    device_id: "pc-1",
  });
  expect(wrapper!.text()).toContain("Waiting for Gaming PC");
  // The status is a separate row beside the control group in the parent action layout.
  expect(wrapper!.get(".launch-group").find("[role=status]").exists()).toBe(
    false,
  );
  expect(wrapper!.get("[role=status]").element.parentElement).toBe(
    wrapper!.get(".launch-group").element.parentElement,
  );
  await vi.advanceTimersByTimeAsync(2100);
  await flushPromises();
  expect(wrapper!.text()).toContain("accepted the launch");
  expect(wrapper!.text()).not.toContain("is running");
});
it("disables offline computers and leaves the local launch available", async () => {
  vi.mocked(fetchLaunchDevices).mockResolvedValue([
    { ...device, online: false },
  ]);
  await openMenu();
  expect(button("Remote start on Gaming PC (offline)").disabled).toBe(true);
  expect(button("Launch in Playnite").disabled).toBe(false);
  expect(queueRemoteLaunch).not.toHaveBeenCalled();
});
it("explains how to connect a computer when no receiver exists", async () => {
  vi.mocked(fetchLaunchDevices).mockResolvedValue([]);
  await openMenu();
  expect(document.body.textContent).toContain(
    "Enable remote launch in the Playnite extension",
  );
});
it("does not expose receiving controls for wishlisted games", async () => {
  wrapper = mount(GameLaunchButton, { props: { game: game("wishlist") } });
  await flushPromises();
  expect(wrapper.find('[aria-label="More ways to launch"]').exists()).toBe(
    false,
  );
  expect(fetchLaunchDevices).not.toHaveBeenCalled();
});
it("shows actionable receiver failure and uncertain-delivery states", async () => {
  vi.mocked(queueRemoteLaunch).mockResolvedValue({
    ...result("failed"),
    failure_code: "not_installed",
  });
  await openMenu();
  button("Remote start on Gaming PC").click();
  await flushPromises();
  expect(wrapper!.text()).toContain("not installed on that computer");
  await wrapper!.get('[aria-label="More ways to launch"]').trigger("click");
  await flushPromises();
  vi.mocked(queueRemoteLaunch).mockResolvedValue(result("unknown"));
  button("Remote start on Gaming PC").click();
  await flushPromises();
  expect(wrapper!.text()).toContain(
    "Check that computer before launching again",
  );
});
it("retries an unconfirmed POST with the same request key and prevents a second launch", async () => {
  vi.mocked(queueRemoteLaunch).mockRejectedValueOnce(new TypeError("offline"));
  await openMenu();
  button("Remote start on Gaming PC").click();
  await flushPromises();
  const original = vi.mocked(queueRemoteLaunch).mock.calls[0]![0];
  expect(wrapper!.text()).toContain("without launching it twice");
  await wrapper!.get('[aria-label="More ways to launch"]').trigger("click");
  await flushPromises();
  expect(button("Remote start on Gaming PC").disabled).toBe(true);
  button("Check this request").click();
  await flushPromises();
  expect(vi.mocked(queueRemoteLaunch).mock.calls[1]![0]).toEqual(original);
});
it("cancels status polling and rejects late responses when navigating to another game", async () => {
  let finish: ((value: RemoteLaunchRequest) => void) | undefined;
  vi.mocked(queueRemoteLaunch).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await openMenu();
  button("Remote start on Gaming PC").click();
  await flushPromises();
  await wrapper!.setProps({ game: { ...game(), id: "game-2" } });
  finish!(result("accepted"));
  await flushPromises();
  expect(wrapper!.text()).not.toContain("accepted the launch");
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetchRemoteLaunch).not.toHaveBeenCalled();
});
it("lets users retry loading computers after a network failure", async () => {
  vi.mocked(fetchLaunchDevices).mockRejectedValueOnce(
    new Error("Network unavailable"),
  );
  await openMenu();
  expect(document.body.textContent).toContain("Network unavailable");
  button("Retry loading computers").click();
  await flushPromises();
  expect(button("Remote start on Gaming PC").disabled).toBe(false);
});
