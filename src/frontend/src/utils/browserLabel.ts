/** A distinguishing default name for this browser, such as "Chrome on Windows". */

interface NavigatorLike {
  userAgent?: string;
  platform?: string;
  userAgentData?: { platform?: string };
}

const BROWSERS: [RegExp, string][] = [
  [/Edg(?:e|A|iOS)?\//, "Edge"],
  [/OPR\/|Opera/, "Opera"],
  [/Vivaldi\//, "Vivaldi"],
  [/SamsungBrowser\//, "Samsung Internet"],
  [/Firefox\/|FxiOS\//, "Firefox"],
  [/Chrome\/|CriOS\//, "Chrome"],
  [/Safari\//, "Safari"],
];

const SYSTEMS: [RegExp, string][] = [
  [/Android/, "Android"],
  [/iPhone|iPad|iPod/, "iOS"],
  [/Windows|Win32|Win64/, "Windows"],
  [/Mac OS X|Macintosh|MacIntel/, "macOS"],
  [/CrOS/, "ChromeOS"],
  [/Linux|X11/, "Linux"],
];

function firstMatch(table: [RegExp, string][], text: string): string {
  return table.find(([pattern]) => pattern.test(text))?.[1] ?? "";
}

export function describeThisBrowser(
  source: NavigatorLike | undefined = typeof navigator === "undefined"
    ? undefined
    : navigator,
): string {
  const userAgent = source?.userAgent ?? "";
  const browser = firstMatch(BROWSERS, userAgent);
  const system =
    firstMatch(SYSTEMS, userAgent) ||
    firstMatch(
      SYSTEMS,
      source?.userAgentData?.platform ?? source?.platform ?? "",
    );
  if (browser && system) return `${browser} on ${system}`;
  return browser || system || "";
}
