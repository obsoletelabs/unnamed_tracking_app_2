// The server renders Markdown to a tree of plain nodes; clients draw it with
// text interpolation only. This module re-checks every link before it is drawn
// so a bug on either side cannot turn content into a script or unsafe URL.

export interface RichNode {
  t: string;
  v?: string;
  c?: RichNode[];
  href?: string;
  level?: number;
}

export type SafeHref =
  { kind: "external"; href: string } | { kind: "route"; href: string };

const HTTPS_URL =
  /^https:\/\/[A-Za-z0-9.-]+\.[A-Za-z][A-Za-z0-9-]*(\/[^\s\\]*)?$/;
const APP_PATH = /^\/(?!\/)[A-Za-z0-9._~!$&'()*+,;=:@/?-]*$/;

export function safeHref(value: string | undefined | null): SafeHref | null {
  if (!value || value.length > 2048) return null;
  if (HTTPS_URL.test(value) && !value.includes("@")) {
    return { kind: "external", href: value };
  }
  if (
    APP_PATH.test(value) &&
    !value.split("?")[0]!.split("/").includes("..") &&
    !/^\/api(\/|$)/.test(value)
  ) {
    return { kind: "route", href: value };
  }
  return null;
}

export function headingTag(level: number | undefined): string {
  return `h${Math.min(Math.max(level ?? 3, 3), 5)}`;
}

export function formatStamp(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString();
}
