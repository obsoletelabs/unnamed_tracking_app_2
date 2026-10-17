// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import SteamOpenIdLink from "../components/settings/SteamOpenIdLink.vue";
import {
  STEAM_OPENID_ENDPOINT,
  beginSteamOpenId,
  steamOpenIdOutcome,
} from "../services/steamOpenId";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(body: unknown, ok = true) {
  const fetch = vi.fn(
    async () => new Response(JSON.stringify(body), { status: ok ? 200 : 409 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

it("starts the redirect with a credentialed, uncached POST and no body", async () => {
  const url = `${STEAM_OPENID_ENDPOINT}?openid.mode=checkid_setup`;
  const fetch = stubFetch({ redirect_url: url });
  expect(await beginSteamOpenId()).toBe(url);
  const [path, options] = fetch.mock.calls[0] as unknown as [
    string,
    RequestInit,
  ];
  expect(path).toBe("/api/settings/steam/openid/begin");
  expect(options).toMatchObject({
    method: "POST",
    credentials: "include",
    cache: "no-store",
  });
  expect(options.body).toBeUndefined();
});

it("refuses to navigate anywhere but Steam's sign-in page", async () => {
  for (const evil of [
    "https://evil.example/openid/login?x=1",
    "http://steamcommunity.com/openid/login?x=1",
    "https://steamcommunity.com.evil.example/openid/login?x=1",
    "javascript:alert(1)",
  ]) {
    stubFetch({ redirect_url: evil });
    await expect(beginSteamOpenId()).rejects.toThrow("invalid Steam");
  }
});

it("surfaces the server's error message", async () => {
  stubFetch({ detail: "Wait a moment." }, false);
  await expect(beginSteamOpenId()).rejects.toThrow("Wait a moment.");
});

it("reads the callback outcome from the query", () => {
  expect(steamOpenIdOutcome({})).toBeNull();
  expect(steamOpenIdOutcome({ steam_openid: "linked" })?.ok).toBe(true);
  expect(
    steamOpenIdOutcome({
      steam_openid: "failed",
      steam_openid_error: "cancelled",
    }),
  ).toEqual({ ok: false, message: "Steam sign-in was cancelled." });
  expect(steamOpenIdOutcome({ steam_openid: "failed" })?.ok).toBe(false);
  expect(steamOpenIdOutcome({ steam_openid: "other" })).toBeNull();
});

it("never renders arbitrary text from the error parameter", () => {
  const generic = "Steam sign-in could not be verified. Try again.";
  for (const crafted of [
    "Your account is locked. Call 555-0100",
    "<img src=x onerror=alert(1)>",
    "__proto__",
    "constructor",
    "toString",
    "",
    ["cancelled-ish"],
    undefined,
  ]) {
    expect(
      steamOpenIdOutcome({
        steam_openid: "failed",
        steam_openid_error: crafted,
      }),
    ).toEqual({ ok: false, message: generic });
  }
});

it("redirects the browser to Steam and states the limits", async () => {
  const url = `${STEAM_OPENID_ENDPOINT}?openid.mode=checkid_setup`;
  stubFetch({ redirect_url: url });
  const assign = vi.fn();
  vi.stubGlobal("location", { assign });
  const wrapper = mount(SteamOpenIdLink, {
    props: { verifiedSteamId: null, hasLibraryAccess: false },
  });
  expect(wrapper.text()).toContain("password is never entered");
  expect(wrapper.text()).toContain("does not let the app read a private");
  await wrapper.get("button").trigger("click");
  await flushPromises();
  expect(assign).toHaveBeenCalledWith(url);
});

it("shows the verified ID and re-verify wording", () => {
  const wrapper = mount(SteamOpenIdLink, {
    props: { verifiedSteamId: "76561198000000001", hasLibraryAccess: true },
  });
  expect(wrapper.text()).toContain("76561198000000001");
  expect(wrapper.get("button").text()).toBe("Verify again with Steam");
});
