import { describe, expect, it } from "vitest";
import {
  disabledSummary,
  isRoutable,
  providerListing,
} from "../utils/notificationProviders";
import type {
  NotificationRoutingDestination,
  NotificationRoutingProvider,
} from "../services/notifications";

const provider = (
  over: Partial<NotificationRoutingProvider> = {},
): NotificationRoutingProvider => ({
  id: "p",
  name: "P",
  enabled: true,
  available: true,
  configuration_scope: "user",
  critical_supported: false,
  ...over,
});
const dest = (
  over: Partial<NotificationRoutingDestination> = {},
): NotificationRoutingDestination => ({
  id: "d",
  provider_id: "p",
  provider_name: "P",
  kind: "x",
  context: "external",
  trust: "PRIVATE",
  active: true,
  enabled: true,
  available: true,
  provider_enabled: true,
  critical_supported: false,
  eligible_types: [],
  shared_configuration: false,
  ...over,
});

describe("provider listing lifecycle", () => {
  it("shows supported providers, configured or not", () => {
    expect(providerListing(provider(), [])).toBe("active");
    expect(providerListing(provider(), [dest()])).toBe("active");
  });
  it("hides unavailable providers without saved destinations", () => {
    expect(providerListing(provider({ available: false }), [])).toBe("hidden");
  });
  it("folds unavailable providers that keep saved destinations", () => {
    expect(
      providerListing(provider({ available: false }), [
        dest({ available: false }),
      ]),
    ).toBe("unavailable");
  });
  it("keeps installed-but-unconfigured providers addable", () => {
    expect(
      providerListing(
        provider({ available: false, configuration_available: true }),
        [],
      ),
    ).toBe("active");
  });
  it("shows configured but switched-off providers compactly and recoverably", () => {
    const off = provider({ enabled: false });
    expect(providerListing(off, [dest()])).toBe("disabled");
    expect(disabledSummary(off, [dest()])).toContain("1 destination ");
    expect(providerListing(off, [])).toBe("active");
  });
  it("never folds the internal inbox", () => {
    expect(
      providerListing(
        provider({ configuration_scope: "internal", enabled: false }),
        [dest()],
      ),
    ).toBe("active");
  });
});

describe("per-type routing choices", () => {
  it("offers only routable destinations", () => {
    expect(isRoutable(dest())).toBe(true);
    expect(isRoutable(dest({ provider_enabled: false }))).toBe(false);
    expect(isRoutable(dest({ enabled: false }))).toBe(false);
    expect(isRoutable(dest({ available: false }))).toBe(false);
    expect(isRoutable(dest({ active: false }))).toBe(false);
  });
});
