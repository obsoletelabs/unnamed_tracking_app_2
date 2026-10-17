// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import SteamConnectionDialog from "../components/settings/SteamConnectionDialog.vue";
import {
  cancelSteamLogin,
  fetchSteamConnection,
  pollSteamLogin,
  SteamConnectionError,
  submitSteamGuard,
} from "../services/steamConnection";
import type { SteamConnection } from "../services/steamConnection";

vi.mock("../services/steamConnection", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/steamConnection")>()),
  fetchSteamConnection: vi.fn(),
  submitSteamGuard: vi.fn(),
  pollSteamLogin: vi.fn(),
  cancelSteamLogin: vi.fn(),
}));

let wrapper: VueWrapper | undefined;

function pendingSignIn(confirmation_types: number[]): SteamConnection {
  return {
    connected: false,
    method: null,
    display_name: null,
    expires_at: null,
    pending: {
      attempt_id: "fixture-attempt",
      expires_at: Date.now() / 1000 + 300,
      interval: 3,
      challenge_url: null,
      confirmation_types,
      phase: "waiting",
    },
  };
}

async function openDialog(confirmation_types: number[]) {
  vi.mocked(fetchSteamConnection).mockResolvedValue(
    pendingSignIn(confirmation_types),
  );
  wrapper = mount(SteamConnectionDialog, { attachTo: document.body });
  await flushPromises();
}

function guardForm() {
  return document.body.querySelector<HTMLFormElement>("form.login-form");
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(submitSteamGuard).mockResolvedValue(pendingSignIn([]));
  vi.mocked(cancelSteamLogin).mockResolvedValue(pendingSignIn([]));
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  vi.useRealTimers();
  vi.clearAllMocks();
});

// Steam's EAuthSessionGuardType: 1 None, 2 EmailCode, 3 DeviceCode,
// 4 DeviceConfirmation, 5 EmailConfirmation.
it("asks mobile authenticator accounts for their Steam Guard app code", async () => {
  await openDialog([4, 3]);
  const form = guardForm();
  expect(form?.textContent).toContain("Steam Guard in your mobile app");
  const input = form!.querySelector("input")!;
  input.value = "ABCDE";
  input.dispatchEvent(new Event("input"));
  form!.dispatchEvent(new Event("submit"));
  await flushPromises();
  expect(submitSteamGuard).toHaveBeenCalledWith("fixture-attempt", "ABCDE", 3);
});

it("asks email Steam Guard accounts for the emailed code", async () => {
  await openDialog([2]);
  const form = guardForm();
  expect(form?.textContent).toContain("code Steam emailed you");
  const input = form!.querySelector("input")!;
  input.value = "FGHIJ";
  input.dispatchEvent(new Event("input"));
  form!.dispatchEvent(new Event("submit"));
  await flushPromises();
  expect(submitSteamGuard).toHaveBeenCalledWith("fixture-attempt", "FGHIJ", 2);
});

it("normalises pasted Steam Guard codes", async () => {
  await openDialog([3]);
  const form = guardForm();
  const input = form!.querySelector("input")!;
  input.value = " ab cde ";
  input.dispatchEvent(new Event("input"));
  form!.dispatchEvent(new Event("submit"));
  await flushPromises();
  expect(submitSteamGuard).toHaveBeenCalledWith("fixture-attempt", "ABCDE", 3);
});

it("keeps the code field usable while a background poll runs", async () => {
  await openDialog([3]);
  vi.mocked(pollSteamLogin).mockReturnValue(new Promise(() => undefined));
  await vi.advanceTimersByTimeAsync(3250);
  expect(pollSteamLogin).toHaveBeenCalledOnce();
  const form = guardForm()!;
  expect(form.querySelector("input")!.disabled).toBe(false);
  expect(form.querySelector("button")!.disabled).toBe(false);
});

it("keeps the chosen Steam Guard method across polls", async () => {
  await openDialog([2, 3]);
  const select = guardForm()!.querySelector("select")!;
  expect(select.value).toBe("3");
  select.value = "2";
  select.dispatchEvent(new Event("change"));
  vi.mocked(pollSteamLogin).mockResolvedValue(pendingSignIn([2, 3]));
  await vi.advanceTimersByTimeAsync(3250);
  await flushPromises();
  expect(pollSteamLogin).toHaveBeenCalled();
  expect(guardForm()!.querySelector("select")!.value).toBe("2");
});

it("retries the same attempt when Steam is briefly unavailable", async () => {
  await openDialog([4]);
  vi.mocked(pollSteamLogin).mockRejectedValueOnce(
    new SteamConnectionError("Could not reach Steam.", 503),
  );
  await vi.advanceTimersByTimeAsync(3250);
  await flushPromises();
  expect(document.body.textContent).toContain("Retrying");
  vi.mocked(pollSteamLogin).mockResolvedValue(pendingSignIn([4]));
  await vi.advanceTimersByTimeAsync(3250);
  expect(pollSteamLogin).toHaveBeenCalledTimes(2);
});

it("shows the real state after the server ends a sign-in", async () => {
  await openDialog([4]);
  vi.mocked(pollSteamLogin).mockRejectedValueOnce(
    new SteamConnectionError(
      "These Steam imports belong to another account.",
      409,
    ),
  );
  vi.mocked(fetchSteamConnection).mockResolvedValue({
    ...pendingSignIn([]),
    pending: null,
  });
  await vi.advanceTimersByTimeAsync(3250);
  await flushPromises();
  expect(document.body.textContent).toContain("belong to another account");
  expect(document.body.textContent).toContain("Show Steam QR code");
  expect(document.body.textContent).not.toContain("Cancel sign-in");
});

it("shows no code form when Steam only offers approval", async () => {
  await openDialog([1]);
  expect(guardForm()).toBeNull();
  wrapper?.unmount();
  await openDialog([4, 5]);
  expect(guardForm()).toBeNull();
});
