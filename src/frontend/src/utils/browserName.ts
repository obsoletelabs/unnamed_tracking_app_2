// A distinguishing default name for this browser's notification destination
// ("Firefox on Linux"), instead of the same "This browser" on every device (#19).
// Only the browser family and the operating system are used: no version, no
// fingerprinting detail, and nothing is sent anywhere until you save.

export function browserLabel(
  userAgent: string,
  platform: string = "",
  brands: readonly { brand: string }[] = [],
): string {
  const browser = brands.some((b) => b.brand === "Brave")
    ? "Brave"
    : /\bEdg(?:e|A|iOS)?\//.test(userAgent)
      ? "Edge"
      : /\bOPR\/|Opera/.test(userAgent)
        ? "Opera"
        : /\b(?:Firefox|FxiOS)\//.test(userAgent)
          ? "Firefox"
          : /\bSamsungBrowser\//.test(userAgent)
            ? "Samsung Internet"
            : /\b(?:Chrome|CriOS|Chromium)\//.test(userAgent)
              ? "Chrome"
              : /\bSafari\//.test(userAgent)
                ? "Safari"
                : "";
  const system = /\bAndroid\b/.test(userAgent)
    ? "Android"
    : /\b(?:iPhone|iPad|iPod)\b/.test(userAgent)
      ? "iOS"
      : /\bCrOS\b/.test(userAgent)
        ? "ChromeOS"
        : /\bWindows\b|^Win/.test(userAgent + " " + platform)
          ? "Windows"
          : /\bMac(?:intosh| OS)\b|^Mac/.test(userAgent + " " + platform)
            ? "macOS"
            : /\bLinux\b|X11/.test(userAgent)
              ? "Linux"
              : "";
  if (browser && system) return `${browser} on ${system}`;
  return browser || (system ? `Browser on ${system}` : "Browser");
}

export function currentBrowserLabel(): string {
  if (typeof navigator === "undefined") return "Browser";
  const data = (
    navigator as Navigator & {
      userAgentData?: { brands?: { brand: string }[] };
    }
  ).userAgentData;
  return browserLabel(
    navigator.userAgent,
    navigator.platform ?? "",
    data?.brands ?? [],
  );
}
