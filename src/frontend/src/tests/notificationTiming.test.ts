// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import NotificationTimingSection from "../components/settings/NotificationTimingSection.vue";
import {
  DEFAULT_PREFERENCES,
  DEFAULT_NOTIFICATION_TIMING,
} from "../services/preferences";
import type { Preferences } from "../services/preferences";

const routing = {
  types: [{ event_type: "media.episode.aired", label: "New episodes" }],
  providers: [],
  destinations: [
    {
      id: "8d0d2f0c-0c51-4d63-a5a4-5d0c2d9b8a11",
      provider_name: "Email",
      label: "me",
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

function prefs(timing = DEFAULT_NOTIFICATION_TIMING): Preferences {
  return { ...DEFAULT_PREFERENCES, notification_timing: timing };
}

async function open(timing = DEFAULT_NOTIFICATION_TIMING, fetch = vi.fn()) {
  fetch.mockImplementation((url: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          url.includes("/preview")
            ? {
                event_type: "media.episode.aired",
                destination_id: routing.destinations[0]!.id,
                category: "media",
                evaluated_at: 1,
                route_enabled: true,
                decision: {
                  action: "hold",
                  mode: "inherit",
                  release_at: 1_800_000_000,
                  bypassed_by_urgency: false,
                  matched_rule: "default",
                  reasons: ["quiet: delivery waits for the end of quiet hours"],
                },
              }
            : routing,
        ),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const wrapper = mount(NotificationTimingSection, {
    props: { prefs: prefs(timing), loaded: true },
  });
  await flushPromises();
  return { wrapper, fetch };
}

it("saves quiet hours through the shared preference change event", async () => {
  const { wrapper } = await open();
  await wrapper.get('[aria-label="Quiet hours"]').trigger("click");
  const [changes] = wrapper.emitted("change")![0] as [Partial<Preferences>];
  expect(changes.notification_timing?.quiet_hours).toEqual({
    enabled: true,
    start: "22:00",
    end: "07:00",
  });
  // The rest of the document is preserved, not reset.
  expect(changes.notification_timing?.digest_times).toEqual(["08:00"]);
});

it("disables the plugin urgency opt-in while urgent interruption is off", async () => {
  const { wrapper } = await open({
    ...DEFAULT_NOTIFICATION_TIMING,
    urgent_threshold: "off",
  });
  expect(
    wrapper
      .get('[aria-label="Let plugins request urgent delivery"]')
      .attributes("disabled"),
  ).toBeDefined();
});

it("adds, edits and removes a rule without dropping other choices", async () => {
  const { wrapper } = await open();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "Add rule")!
    .trigger("click");
  const added = (wrapper.emitted("change")![0] as [Partial<Preferences>])[0];
  expect(added.notification_timing?.rules).toEqual([
    { category: "*", destination_id: null, mode: "inherit", urgent: "inherit" },
  ]);
  await wrapper.setProps({ prefs: prefs(added.notification_timing) });
  await wrapper.get('[aria-label="Rule 1 mode"]').setValue("mute");
  const edited = (wrapper.emitted("change")![1] as [Partial<Preferences>])[0];
  expect(edited.notification_timing?.rules?.[0]?.mode).toBe("mute");
  await wrapper.setProps({ prefs: prefs(edited.notification_timing) });
  await wrapper.get('[aria-label="Remove rule 1"]').trigger("click");
  const removed = (wrapper.emitted("change")![2] as [Partial<Preferences>])[0];
  expect(removed.notification_timing?.rules).toEqual([]);
});

it("explains the effective decision from the server preview", async () => {
  const { wrapper, fetch } = await open();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "Explain")!
    .trigger("click");
  await flushPromises();
  expect(fetch.mock.calls.at(-1)![0]).toContain(
    "/api/settings/notification-timing/preview?event_type=media.episode.aired",
  );
  expect(wrapper.get('[role="status"]').text()).toContain("Held until");
  expect(wrapper.text()).toContain("waits for the end of quiet hours");
});
