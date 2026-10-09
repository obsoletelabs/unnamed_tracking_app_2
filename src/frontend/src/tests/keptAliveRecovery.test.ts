import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";
import { useKeptAlive } from "../utils/useKeptAlive";

const hooks = vi.hoisted(() => ({
  mounted: [] as (() => void)[],
  activated: [] as (() => void)[],
  deactivated: [] as (() => void)[],
  unmounted: [] as (() => void)[],
}));
vi.mock("vue", async (original) => ({
  ...(await original<typeof import("vue")>()),
  onMounted: (fn: () => void) => hooks.mounted.push(fn),
  onActivated: (fn: () => void) => hooks.activated.push(fn),
  onDeactivated: (fn: () => void) => hooks.deactivated.push(fn),
  onUnmounted: (fn: () => void) => hooks.unmounted.push(fn),
}));
let scope = effectScope();
let loading = ref(false);
let error = ref(false);
let online = { onLine: true };
let visibility = Object.assign(new EventTarget(), {
  visibilityState: "visible",
});
const refresh = vi.fn(() => {
  loading.value = true;
});
function mount(recover = true) {
  scope.run(() =>
    useKeptAlive(
      refresh,
      recover
        ? {
            isLoading: () => loading.value,
            hasError: () => error.value,
          }
        : undefined,
    ),
  );
  hooks.mounted.forEach((fn) => fn());
  hooks.activated.forEach((fn) => fn());
}
beforeEach(() => {
  scope = effectScope();
  loading = ref(false);
  error = ref(false);
  online = { onLine: true };
  visibility = Object.assign(new EventTarget(), { visibilityState: "visible" });
  vi.stubGlobal("window", new EventTarget());
  vi.stubGlobal("document", visibility);
  vi.stubGlobal("navigator", online);
});
afterEach(() => {
  hooks.unmounted.forEach((fn) => fn());
  Object.values(hooks).forEach((fns) => {
    fns.length = 0;
  });
  scope.stop();
  refresh.mockClear();
  vi.unstubAllGlobals();
});

describe("kept-alive library connection recovery", () => {
  it("coalesces reconnect, visibility and focus until the request finishes", async () => {
    mount();
    error.value = true;
    window.dispatchEvent(new Event("online"));
    window.dispatchEvent(new Event("focus"));
    visibility.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).toHaveBeenCalledTimes(1);
    error.value = false;
    loading.value = false;
    await nextTick();
    window.dispatchEvent(new Event("focus"));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("waits for an in-flight request before retrying a reconnect", async () => {
    mount();
    loading.value = true;
    await nextTick();
    window.dispatchEvent(new Event("online"));
    expect(refresh).not.toHaveBeenCalled();
    loading.value = false;
    await nextTick();
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("defers hidden-tab recovery until the tab is visible", () => {
    mount();
    visibility.visibilityState = "hidden";
    window.dispatchEvent(new Event("online"));
    expect(refresh).not.toHaveBeenCalled();
    visibility.visibilityState = "visible";
    visibility.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("does not refresh cached inactive libraries on connection events", () => {
    mount();
    hooks.deactivated.forEach((fn) => fn());
    window.dispatchEvent(new Event("online"));
    error.value = true;
    window.dispatchEvent(new Event("focus"));
    expect(refresh).not.toHaveBeenCalled();
    hooks.activated.forEach((fn) => fn());
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it("stops listening after unmount", () => {
    mount();
    hooks.unmounted.forEach((fn) => fn());
    window.dispatchEvent(new Event("online"));
    expect(refresh).not.toHaveBeenCalled();
  });
  it("keeps healthy focus changes and opted-out pages free of new requests", () => {
    mount();
    window.dispatchEvent(new Event("focus"));
    expect(refresh).not.toHaveBeenCalled();
    hooks.unmounted.forEach((fn) => fn());
    Object.values(hooks).forEach((fns) => {
      fns.length = 0;
    });
    mount(false);
    window.dispatchEvent(new Event("online"));
    expect(refresh).not.toHaveBeenCalled();
  });
  it("does not retry while offline and retries a failed load on focus", () => {
    mount();
    error.value = true;
    online.onLine = false;
    window.dispatchEvent(new Event("focus"));
    expect(refresh).not.toHaveBeenCalled();
    online.onLine = true;
    window.dispatchEvent(new Event("focus"));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
