import { beforeEach, describe, expect, it } from "vitest";
import {
  classifySetupStatus,
  consumeReturnPath,
  rememberReturnPath,
  safeReturnPath,
  startupState,
  publicEntryAllowed,
} from "../state/startup";

const storage = new Map<string, string>();
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
  },
});

describe("startup state", () => {
  it("renders public invitation acceptance while guarding authenticated pages", () => {
    for (const page of [
      "accept-invite",
      "password-reset",
      "login",
      "local-login",
      "oidc-start",
      "oidc-provider-start",
    ])
      expect(publicEntryAllowed("auth-required", page)).toBe(true);
    for (const state of [
      "checking",
      "unavailable",
      "ready",
      "setup-required",
    ] as const)
      expect(publicEntryAllowed(state, "accept-invite")).toBe(false);
    expect(publicEntryAllowed("auth-required", "games")).toBe(false);
    expect(publicEntryAllowed("setup-required", "setup")).toBe(true);
    expect(publicEntryAllowed("auth-required", "setup")).toBe(false);
  });
  beforeEach(() => {
    sessionStorage.clear();
    startupState.value = "checking";
  });

  it("distinguishes confirmed setup from an available configured backend", () => {
    expect(classifySetupStatus({ setup_required: true })).toBe(
      "setup-required",
    );
    expect(classifySetupStatus({ setup_required: false })).toBe("ready");
  });

  it("preserves internal paths with query parameters", () => {
    const path = "/settings?section=admin-sessions";
    expect(safeReturnPath(path)).toBe(path);
    rememberReturnPath(path);
    expect(consumeReturnPath(undefined)).toBe(path);
  });

  it("rejects external and protocol-relative return paths", () => {
    expect(safeReturnPath("https://malicious.example/")).toBeNull();
    expect(safeReturnPath("//malicious.example/")).toBeNull();
    expect(safeReturnPath("/bad external value")).toBeNull();
    expect(safeReturnPath("javascript:alert(1)")).toBeNull();
  });

  it.each([
    "/",
    "/?welcome=1#top",
    "/login",
    "/login/",
    "/login/local",
    "/login/oidcstart?return_to=/games",
    "/login/provider",
    "/setup",
    "/setup/",
    "/accept-invite#id=credential",
    "/reset-password#token=credential",
    "/setup?return_to=/games",
  ])("does not remember the entry page %s as a destination", (path) => {
    expect(safeReturnPath(path)).toBeNull();
    expect(rememberReturnPath(path)).toBeNull();
    expect(consumeReturnPath(path)).toBeNull();
  });

  it("removes obsolete entry-page destinations from storage", () => {
    sessionStorage.setItem("unnamedTracking.startupReturnPath", "/login");
    expect(consumeReturnPath(undefined)).toBeNull();
    expect(
      sessionStorage.getItem("unnamedTracking.startupReturnPath"),
    ).toBeNull();
  });

  it("falls back safely when the return path is missing or malformed", () => {
    expect(consumeReturnPath(undefined)).toBeNull();
    expect(consumeReturnPath("not a route")).toBeNull();
    rememberReturnPath("/games/example");
    expect(consumeReturnPath("/bad external value")).toBe("/games/example");
  });

  it("consumes a stored return path only once", () => {
    rememberReturnPath("/games/example?tab=history");
    expect(consumeReturnPath(undefined)).toBe("/games/example?tab=history");
    expect(consumeReturnPath(undefined)).toBeNull();
  });
});
