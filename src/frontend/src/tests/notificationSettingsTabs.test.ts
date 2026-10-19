// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { afterEach, expect, it, vi } from "vitest";
import type { SettingsGroup } from "../components/settings/SettingsNav.vue";
import NotificationSettingsSection from "../components/settings/NotificationSettingsSection.vue";
import {
  NOTIFICATION_SETTINGS_TABS,
  buildSettingsSearchIndex,
  resolveSettingsSection,
  searchSettings,
} from "../state/settingsRegistry";

vi.mock("../services/preferences", async (original) => ({
  ...(await original<typeof import("../services/preferences")>()),
  fetchPreferences: vi.fn().mockResolvedValue({}),
  queuePreferences: vi.fn(),
}));
vi.mock("../services/notifications", async (original) => ({
  ...(await original<typeof import("../services/notifications")>()),
  fetchInboxRetentionPolicy: vi.fn().mockResolvedValue(null),
}));

const stub = (name: string) =>
  defineComponent({
    name,
    props: { part: { type: String, default: "" } },
    render() {
      return h("div", { "data-stub": name, "data-part": this.part });
    },
  });
const stubs = {
  NotificationRoutingSection: stub("routing"),
  NotificationTimingSection: stub("timing"),
  NotificationDeliveryHistory: stub("history"),
};

afterEach(() => vi.clearAllMocks());

function shown(wrapper: ReturnType<typeof mount>) {
  return wrapper
    .findAll("[data-stub]")
    .map((el) => `${el.attributes("data-stub")}:${el.attributes("data-part")}`);
}

it("splits the notification settings into short pages instead of one long scroll (#273)", async () => {
  const wrapper = mount(NotificationSettingsSection, {
    global: { stubs },
  });
  await flushPromises();
  expect(wrapper.findAll('[role="tab"]').map((tab) => tab.text())).toEqual(
    NOTIFICATION_SETTINGS_TABS.map((tab) => tab.label),
  );
  // only the providers appear first; the other pages are not rendered at all
  expect(shown(wrapper)).toEqual(["routing:providers"]);

  const tabs = wrapper.findAll('[role="tab"]');
  await tabs[1].trigger("click");
  expect(shown(wrapper)).toEqual(["routing:types"]);
  await tabs[2].trigger("click");
  expect(shown(wrapper)).toEqual(["timing:"]);
  await tabs[3].trigger("click");
  expect(shown(wrapper)).toEqual(["history:"]);
  expect(wrapper.text()).toContain("Inbox history");
  expect(wrapper.find('[aria-selected="true"]').text()).toBe("History");
});

it("opens the page a link or a search result asks for, and falls back to providers", async () => {
  const timing = mount(NotificationSettingsSection, {
    props: { initialTab: "timing" },
    global: { stubs },
  });
  await flushPromises();
  expect(shown(timing)).toEqual(["timing:"]);
  const unknown = mount(NotificationSettingsSection, {
    props: { initialTab: "nope" },
    global: { stubs },
  });
  await flushPromises();
  expect(shown(unknown)).toEqual(["routing:providers"]);
});

it("keeps old links and search working for the new pages", () => {
  // the retired calendar-notification page now lands on the types page
  expect(resolveSettingsSection("calendar-notifications")).toEqual({
    section: "notifications",
    tab: "types",
  });
  const groups: SettingsGroup[] = [
    {
      area: "preferences",
      label: "Preferences",
      sections: [{ id: "notifications", label: "Notifications" }],
    },
  ] as SettingsGroup[];
  const index = buildSettingsSearchIndex(groups);
  const hit = searchSettings(index, "quiet hours")[0];
  expect(hit).toMatchObject({ section: "notifications", tab: "timing" });
  expect(searchSettings(index, "retry")[0]).toMatchObject({
    section: "notifications",
    tab: "history",
  });
});
