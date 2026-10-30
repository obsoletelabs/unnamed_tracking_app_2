import { afterEach, describe, expect, it, vi } from "vitest";
import {
  pluginExternalDestination,
  requireUserActivation,
  type UiAction,
} from "./pluginUi";

const action = { external_navigation: true } as UiAction;
describe("plugin external navigation", () => {
  it("uses an explicit declared HTTP destination", () => {
    expect(
      pluginExternalDestination(action, {
        redirect_url: "https://media.example/web/index.html#!/details?id=123",
      }),
    ).toBe("https://media.example/web/index.html#!/details?id=123");
    expect(
      pluginExternalDestination({} as UiAction, {
        redirect_url: "https://media.example",
      }),
    ).toBeNull();
    expect(pluginExternalDestination(action, { ok: false })).toBeNull();
    expect(
      pluginExternalDestination(action, {
        ok: false,
        redirect_url: "https://media.example",
      }),
    ).toBeNull();
  });
  it("rejects executable schemes and embedded credentials", () => {
    for (const redirect_url of [
      "javascript:alert(1)",
      "file:///etc/passwd",
      "https://secret@host",
    ]) {
      expect(() =>
        pluginExternalDestination(action, { redirect_url }),
      ).toThrow();
    }
  });
});

describe("a plugin frame asking to leave the app", () => {
  afterEach(() => vi.unstubAllGlobals());
  const frameMessage = (isActive: boolean) =>
    vi.stubGlobal("navigator", { userActivation: { isActive } });

  it("is refused when it did not follow a click", () => {
    frameMessage(false);
    expect(() => requireUserActivation(action)).toThrow("after you click");
  });

  it("is allowed after a click, and for actions that stay in the app", () => {
    frameMessage(true);
    expect(() => requireUserActivation(action)).not.toThrow();
    frameMessage(false);
    expect(() => requireUserActivation({} as UiAction)).not.toThrow();
  });

  it("gives browsers that cannot tell the benefit of the doubt", () => {
    vi.stubGlobal("navigator", {});
    expect(() => requireUserActivation(action)).not.toThrow();
  });
});
