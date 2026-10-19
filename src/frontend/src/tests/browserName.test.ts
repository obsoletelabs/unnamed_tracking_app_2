import { describe, expect, it } from "vitest";
import { browserLabel } from "../utils/browserName";

const UA = {
  firefoxLinux:
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
  chromeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
  edgeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
  safariMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  safariIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  chromeAndroid:
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
  operaWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 OPR/114.0.0.0",
};

describe("a distinguishing default label for this browser (#19)", () => {
  it.each([
    [UA.firefoxLinux, "Firefox on Linux"],
    [UA.chromeWindows, "Chrome on Windows"],
    [UA.edgeWindows, "Edge on Windows"],
    [UA.safariMac, "Safari on macOS"],
    [UA.safariIphone, "Safari on iOS"],
    [UA.chromeAndroid, "Chrome on Android"],
    [UA.operaWindows, "Opera on Windows"],
  ])("names %#", (ua, expected) => {
    expect(browserLabel(ua)).toBe(expected);
  });

  it("recognises Brave through its brand list and never invents detail", () => {
    expect(browserLabel(UA.chromeWindows, "Win32", [{ brand: "Brave" }])).toBe(
      "Brave on Windows",
    );
    expect(browserLabel("")).toBe("Browser");
    expect(browserLabel("SomeBot/1.0 (X11; Linux)")).toBe("Browser on Linux");
    expect(browserLabel(UA.firefoxLinux)).not.toMatch(/\d/); // no version numbers
  });
});
