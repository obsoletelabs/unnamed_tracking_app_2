// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import NotificationSettingsSection from "../components/settings/NotificationSettingsSection.vue";
import AdminNotificationSettings from "../components/settings/AdminNotificationSettings.vue";
import {
  ADMIN_NOTIFICATION_SETTINGS_TABS,
  NOTIFICATION_SETTINGS_TABS,
} from "../state/settingsRegistry";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const routing = vi.hoisted(() => ({
  route: { query: {} as Record<string, string> },
}));
vi.mock("vue-router", async (original) => ({
  ...(await original<typeof import("vue-router")>()),
  useRoute: () => routing.route,
}));
vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn(async () => ({ ...DEFAULT_PREFERENCES })),
}));
vi.mock("../services/notifications", async (original) => ({
  ...(await original<typeof import("../services/notifications")>()),
  fetchInboxRetentionPolicy: vi.fn(async () => ({
    default_days: 30,
    maximum_days: 0,
    effective_days: 30,
    limited: false,
  })),
  fetchNotificationRouting: vi.fn(async () => ({
    types: [],
    providers: [],
    destinations: [],
  })),
}));

const stubs = {
  NotificationRoutingSection: true,
  NotificationTimingSection: true,
  NotificationDeliveryHistory: true,
  SmtpSettingsSection: true,
  BrowserPushSettingsSection: true,
  RouterLink: { template: "<a><slot /></a>" },
};

afterEach(() => {
  routing.route = { query: {} };
});

async function open(
  component: typeof NotificationSettingsSection,
  initialTab?: string,
) {
  const wrapper = mount(component, {
    props: { initialTab },
    global: { stubs },
  });
  await flushPromises();
  return wrapper;
}

describe("Account notification sub-pages", () => {
  it("offers one tab per context and opens providers by default", async () => {
    const wrapper = await open(NotificationSettingsSection);
    expect(wrapper.findAll('[role="tab"]').map((t) => t.text())).toEqual(
      NOTIFICATION_SETTINGS_TABS.map((t) => t.label),
    );
    const routingStub = wrapper.get("notification-routing-section-stub");
    expect(routingStub.attributes("part")).toBe("providers");
    expect(wrapper.find("notification-timing-section-stub").exists()).toBe(
      false,
    );
  });

  it("renders only the sections of each tab", async () => {
    const types = await open(NotificationSettingsSection, "types");
    expect(
      types.get("notification-routing-section-stub").attributes("part"),
    ).toBe("types");

    const timing = await open(NotificationSettingsSection, "timing");
    expect(timing.find("notification-timing-section-stub").exists()).toBe(true);
    expect(timing.find("notification-routing-section-stub").exists()).toBe(
      false,
    );

    const history = await open(NotificationSettingsSection, "history");
    expect(history.find("notification-delivery-history-stub").exists()).toBe(
      true,
    );
    expect(history.text()).toContain("Keep inbox history");
    expect(history.find("notification-routing-section-stub").exists()).toBe(
      false,
    );
  });

  it("switches tabs without leaving the page", async () => {
    const wrapper = await open(NotificationSettingsSection);
    const timingTab = wrapper
      .findAll('[role="tab"]')
      .find((t) => t.text() === "Timing & quiet hours")!;
    await timingTab.trigger("click");
    expect(timingTab.attributes("aria-selected")).toBe("true");
    expect(wrapper.find("notification-timing-section-stub").exists()).toBe(
      true,
    );
  });
});

describe("Administration notification sub-pages", () => {
  it("offers one tab per context and opens email by default", async () => {
    const wrapper = await open(AdminNotificationSettings);
    expect(wrapper.findAll('[role="tab"]').map((t) => t.text())).toEqual(
      ADMIN_NOTIFICATION_SETTINGS_TABS.map((t) => t.label),
    );
    expect(wrapper.find("smtp-settings-section-stub").exists()).toBe(true);
    expect(wrapper.find("browser-push-settings-section-stub").exists()).toBe(
      false,
    );
  });

  it("renders only the sections of each tab", async () => {
    const push = await open(AdminNotificationSettings, "push");
    expect(push.find("browser-push-settings-section-stub").exists()).toBe(true);
    expect(push.find("smtp-settings-section-stub").exists()).toBe(false);

    const plugins = await open(AdminNotificationSettings, "plugins");
    expect(plugins.text()).toContain("Install and grant");
    expect(plugins.text()).not.toContain("Server default");

    const retention = await open(AdminNotificationSettings, "retention");
    expect(retention.text()).toContain("NOTIFICATION_RETENTION_DEFAULT_DAYS");
    expect(retention.text()).not.toContain("Install and grant");
  });

  it("opens plugin providers for a provider deep link", async () => {
    routing.route = { query: { notification_provider: "plugin.example" } };
    const wrapper = await open(AdminNotificationSettings);
    expect(wrapper.text()).toContain("Install and grant");
    expect(wrapper.find("smtp-settings-section-stub").exists()).toBe(false);
  });
});
