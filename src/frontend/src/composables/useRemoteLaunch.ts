import { onBeforeUnmount, ref, watch, type Ref } from "vue";
import { ApiRequestError } from "../services/apiError";
import {
  fetchLaunchDevices,
  fetchRemoteLaunch,
  queueRemoteLaunch,
  remoteLaunchMessage,
  type LaunchCommand,
  type LaunchDevice,
  type RemoteLaunchRequest,
} from "../services/remoteLaunch";

function requestKey(): string {
  // getRandomValues also works on LAN HTTP origins, where randomUUID may be unavailable.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 15) | 64;
  bytes[8] = (bytes[8]! & 63) | 128;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}
export function useRemoteLaunch(gameId: Readonly<Ref<string>>) {
  const devices = ref<LaunchDevice[]>([]);
  const loading = ref(false);
  const deviceError = ref("");
  const message = ref("");
  const error = ref("");
  const busy = ref(false);
  const retryable = ref(false);
  let listing: AbortController | null = null;
  let action: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: {
    command: LaunchCommand;
    name: string;
    started: number;
    result: RemoteLaunchRequest | null;
  } | null = null;

  function reset() {
    listing?.abort();
    action?.abort();
    clearTimeout(timer);
    listing = action = null;
    pending = null;
    devices.value = [];
    loading.value = false;
    busy.value = false;
    retryable.value = false;
    deviceError.value = message.value = error.value = "";
  }
  watch(gameId, reset);
  onBeforeUnmount(reset);

  async function loadDevices() {
    listing?.abort();
    const controller = new AbortController();
    listing = controller;
    loading.value = true;
    deviceError.value = "";
    try {
      const rows = await fetchLaunchDevices(controller.signal);
      if (!controller.signal.aborted) devices.value = rows;
    } catch (cause) {
      if (!controller.signal.aborted) {
        devices.value = [];
        deviceError.value =
          cause instanceof Error ? cause.message : "Could not load computers.";
      }
    } finally {
      if (listing === controller) loading.value = false;
    }
  }
  function applyResult(result: RemoteLaunchRequest) {
    if (!pending) return;
    pending.result = result;
    message.value = remoteLaunchMessage(result, pending.name);
    error.value = "";
    retryable.value = false;
    busy.value = result.state === "pending" || result.state === "dispatched";
    if (!busy.value) {
      pending = null;
      return;
    }
    schedulePoll(2000);
  }
  function schedulePoll(delay: number) {
    clearTimeout(timer);
    timer = setTimeout(() => {
      void poll();
    }, delay);
  }
  async function poll() {
    const current = pending;
    const controller = action;
    if (!current?.result || !controller || controller.signal.aborted) return;
    try {
      const result = await fetchRemoteLaunch(
        current.result.id,
        controller.signal,
      );
      if (pending === current && !controller.signal.aborted)
        applyResult(result);
    } catch (cause) {
      if (pending !== current || controller.signal.aborted) return;
      error.value =
        "Could not check the launch result. The game may have started. Check its status before trying another launch.";
      retryable.value = true;
      if (Date.now() < current.result.expires_at * 1000 + 15000)
        schedulePoll(5000);

      if (
        cause instanceof ApiRequestError &&
        (cause.status === 401 || cause.status === 403 || cause.status === 404)
      ) {
        clearTimeout(timer);
      }
    }
  }
  async function submit() {
    const current = pending;
    const controller = action;
    if (!current || !controller || controller.signal.aborted) return;
    busy.value = true;
    error.value = "";
    retryable.value = false;
    try {
      const result = await queueRemoteLaunch(
        current.command,
        controller.signal,
      );
      if (pending === current && !controller.signal.aborted)
        applyResult(result);
    } catch (cause) {
      if (pending !== current || controller.signal.aborted) return;
      busy.value = false;
      if (cause instanceof ApiRequestError && cause.status < 500) {
        error.value = cause.message;
        pending = null;
        void loadDevices();
      } else {
        busy.value = true;
        error.value =
          "Could not confirm whether the request reached the server. Retry this request to check without launching it twice.";
        retryable.value = true;
      }
    }
  }
  async function launch(device: LaunchDevice) {
    if (pending || busy.value || !device.online) return;
    action?.abort();
    clearTimeout(timer);
    action = new AbortController();
    pending = {
      command: {
        device_id: device.id,
        game_id: gameId.value,
        request_key: requestKey(),
      },
      name: device.name,
      started: Date.now(),
      result: null,
    };
    message.value = "Sending launch request to " + device.name + "…";
    await submit();
  }
  async function retry() {
    if (!pending) return;
    clearTimeout(timer);
    if (pending.result) {
      await poll();
      return;
    }
    if (Date.now() - pending.started >= 60000) {
      message.value =
        "The unconfirmed request has expired. Check the computer before launching again.";
      pending = null;
      retryable.value = false;
      busy.value = false;
      error.value = "";
      return;
    }
    await submit();
  }
  return {
    devices,
    loading,
    deviceError,
    message,
    error,
    busy,
    retryable,
    loadDevices,
    launch,
    retry,
  };
}
