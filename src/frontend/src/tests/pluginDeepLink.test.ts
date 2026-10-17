import { describe, expect, it } from "vitest";
import {
  deepLinkedPluginId,
  hasPluginEntry,
  settingsLinkNeedsFallback,
} from "../services/pluginDeepLink";
import { notificationDestination } from "../utils/notificationPresentation";

describe("plugin deep links", () => {
  it("accepts plain plugin ids and rejects malformed values", () => {
    expect(deepLinkedPluginId("media.sync")).toBe("media.sync");
    expect(deepLinkedPluginId(["a.b", "c"])).toBe("a.b");
    expect(deepLinkedPluginId("")).toBeNull();
    expect(deepLinkedPluginId(undefined)).toBeNull();
    expect(deepLinkedPluginId('x"><script>')).toBeNull();
    expect(deepLinkedPluginId("a".repeat(300))).toBeNull();
  });

  it("finds installed or catalogue entries only", () => {
    const entries = [{ plugin_id: "media.sync" }];
    expect(hasPluginEntry(entries, "media.sync")).toBe(true);
    expect(hasPluginEntry(entries, "other")).toBe(false);
    expect(hasPluginEntry(entries, null)).toBe(false);
  });

  it("sends non-admins to the fallback for admin locations without revealing the plugin", () => {
    expect(settingsLinkNeedsFallback(false, "plugins", "administration")).toBe(
      true,
    );
    expect(settingsLinkNeedsFallback(false, "plugins", undefined)).toBe(true);
    expect(settingsLinkNeedsFallback(false, "profile", "account")).toBe(false);
    expect(settingsLinkNeedsFallback(true, "plugins", "administration")).toBe(
      false,
    );
  });

  it("uses a same-origin notification link and ignores external ones", () => {
    const base = { mediaType: "plugin", mediaId: "id" };
    const link = "/settings?area=administration&section=plugins&plugin=a.b";
    expect(notificationDestination({ ...base, link })).toBe(link);
    expect(
      notificationDestination({ ...base, link: "//evil.test" }),
    ).toBeNull();
    expect(
      notificationDestination({ ...base, link: "https://evil.test" }),
    ).toBeNull();
    expect(notificationDestination({ ...base, link: null })).toBeNull();
  });
});
