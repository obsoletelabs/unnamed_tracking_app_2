import { describe, expect, it } from "vitest";
import { describeThisBrowser } from "../utils/browserLabel";

const agents: [string, string][] = [
  [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Chrome on Windows",
  ],
  [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Edge on Windows",
  ],
  [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Safari on macOS",
  ],
  [
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Firefox on Linux",
  ],
  [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
    "Chrome on Android",
  ],
  [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1",
    "Chrome on iOS",
  ],
];

describe("describeThisBrowser", () => {
  it.each(agents)("names %s", (userAgent, expected) => {
    expect(describeThisBrowser({ userAgent })).toBe(expected);
  });

  it("falls back to the platform when the agent is unrecognised", () => {
    expect(describeThisBrowser({ userAgent: "", platform: "Win32" })).toBe(
      "Windows",
    );
  });

  it("returns an empty name rather than a generic default", () => {
    expect(describeThisBrowser({})).toBe("");
    expect(describeThisBrowser(undefined)).toBe(describeThisBrowser(navigator));
  });
});
