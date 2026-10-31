import { afterEach, expect, it, vi } from "vitest";
import {
  requirePluginSaveGesture,
  requireUserActivation,
  type UiAction,
} from "../services/pluginUi";

function activation(isActive: boolean) {
  vi.stubGlobal("navigator", { userActivation: { isActive } });
}

afterEach(() => vi.unstubAllGlobals());

it("lets a plugin frame save secrets or settings only after a click", () => {
  activation(false);
  expect(() => requirePluginSaveGesture()).toThrow(/after you click/);
  activation(true);
  expect(() => requirePluginSaveGesture()).not.toThrow();
});

it("still requires a click only for external-navigation actions", () => {
  activation(false);
  const action = { id: "go", external_navigation: true } as UiAction;
  expect(() => requireUserActivation(action)).toThrow(/another site/);
  expect(() =>
    requireUserActivation({ ...action, external_navigation: false }),
  ).not.toThrow();
});
