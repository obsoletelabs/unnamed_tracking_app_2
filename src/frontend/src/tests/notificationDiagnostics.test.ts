// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import NotificationDiagnosticsPanel from "../components/settings/NotificationDiagnosticsPanel.vue";
import NotificationDeliveryHistory from "../components/settings/NotificationDeliveryHistory.vue";

const overview = {
  fixtures: [
    {
      id: "standard",
      label: "Ordinary release",
      description: "A normal release.",
      sendable: true,
      sensitive: false,
    },
    {
      id: "recovery",
      label: "Account recovery (simulation only)",
      description: "Never sent.",
      sendable: false,
      sensitive: true,
    },
  ],
  variants: ["basic", "rich"],
  failures: ["none", "transient"],
  providers: [
    {
      provider_id: "core.smtp",
      name: "Email",
      kind: "built_in",
      health: {
        status: "ok",
        code: "ok",
        label: "Ready",
        action: "",
        probe: "configuration",
      },
      profile: {
        renderer: "legacy",
        critical_supported: true,
        notes: [],
        capabilities: {},
      },
      last_24h: { statuses: { sent: 2 }, error_codes: {} },
    },
  ],
  retry_policy: {
    max_attempts: 3,
    backoff_seconds: [60, 120],
    lease_seconds: 90,
    transport_timeout_seconds: 35,
    delivery_semantics: "at_least_once",
  },
  max_test_batch: 5,
};
const routing = {
  types: [],
  providers: [],
  destinations: [
    {
      id: "d1",
      provider_id: "core.smtp",
      provider_name: "Email",
      label: "me",
      context: "external",
    },
  ],
};
const stage = (name: string, code: string) => ({
  stage: name,
  code,
  label: `${name} label`,
  action: `${name} action`,
  detail: {},
});

afterEach(() => vi.unstubAllGlobals());

function stubFetch(
  extra: (url: string, init?: RequestInit) => unknown = () => null,
) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const custom = extra(url, init);
      const body =
        custom ??
        (url.endsWith("/overview")
          ? overview
          : url.includes("notification-providers/destinations")
            ? routing
            : {});
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => "",
      };
    }),
  );
  return calls;
}

async function panel(extra?: (url: string, init?: RequestInit) => unknown) {
  const calls = stubFetch(extra);
  const wrapper = mount(NotificationDiagnosticsPanel);
  await flushPromises();
  return { wrapper, calls };
}

it("labels dry runs and shows every stage with its next action", async () => {
  const { wrapper, calls } = await panel((url) =>
    url.endsWith("/simulate")
      ? {
          dry_run: true,
          fixture: "standard",
          stages: [
            stage("ingestion", "accepted"),
            stage("grouping", "coalesce"),
          ],
          destinations: [
            {
              destination_id: "d1",
              provider_id: "core.smtp",
              name: "me",
              stages: [
                stage("eligibility", "eligible"),
                stage("delivery", "sent"),
              ],
            },
          ],
        }
      : null,
  );
  const run = wrapper.findAll("button").find((b) => b.text() === "Run dry run");
  await run!.trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Dry run — nothing was queued or sent");
  for (const word of ["Ingestion", "Grouping", "Eligibility", "Delivery"])
    expect(wrapper.text()).toContain(word);
  expect(wrapper.text()).toContain("Next: delivery action");
  const request = calls.find((c) => c.url.endsWith("/simulate"))!;
  expect(JSON.parse(String(request.init?.body))).toEqual({
    fixture: "standard",
  });
});

it("requires explicit confirmation before a test send", async () => {
  const { wrapper, calls } = await panel((url) =>
    url.endsWith("/test-send")
      ? { labelled: "test", queued: 2, deliveries: [] }
      : null,
  );
  const send = () =>
    wrapper.findAll("button").find((b) => b.text() === "Send test")!;
  expect(send().attributes("disabled")).toBeDefined();
  await wrapper.find('input[type="checkbox"]').setValue(true);
  expect(send().attributes("disabled")).toBeUndefined();
  await send().trigger("click");
  await flushPromises();
  const call = calls.find((c) => c.url.endsWith("/test-send"))!;
  expect(JSON.parse(String(call.init?.body))).toMatchObject({
    destination_id: "d1",
    confirm: true,
    batch_size: 1,
  });
  expect(wrapper.text()).toContain("Queued 2 test message(s)");
  // The confirmation resets so a second send needs a new decision.
  expect(send().attributes("disabled")).toBeDefined();
});

it("exposes labelled controls, headings and live result regions", async () => {
  const { wrapper } = await panel();
  expect(wrapper.find("section").attributes("aria-labelledby")).toBe(
    "diag-title",
  );
  expect(wrapper.find("#diag-title").exists()).toBe(true);
  for (const control of wrapper.findAll("select, input")) {
    const labelled = control.element.closest("label") !== null;
    expect(labelled, `${control.html()} needs a label`).toBe(true);
  }
});

it("shows a rejected request as an alert", async () => {
  stubFetch();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 403,
      json: async () => ({}),
      text: async () => "{}",
    })),
  );
  const wrapper = mount(NotificationDiagnosticsPanel);
  await flushPromises();
  expect(wrapper.find('[role="alert"]').exists()).toBe(true);
});

const delivery = (changes = {}) => ({
  id: "x1",
  event_type: "notification.provider.test",
  purpose: "test",
  test: true,
  provider_id: "core.smtp",
  destination: "me",
  status: "failed_permanent",
  operation: "send",
  attempts: 1,
  max_attempts: 3,
  reason: {
    code: "invalid_config",
    label: "The provider is not configured correctly.",
    action: "Review and save the provider's configuration, then retry.",
  },
  fallbacks: [],
  next_attempt_at: null,
  retry_allowed: true,
  attempt_log: [],
  ...changes,
});

it("explains a failed delivery and offers a bounded retry only when allowed", async () => {
  const calls = stubFetch((url) =>
    url.includes("/deliveries")
      ? {
          deliveries: [
            delivery(),
            delivery({ id: "x2", retry_allowed: false }),
          ],
          retry_policy: overview.retry_policy,
        }
      : null,
  );
  const wrapper = mount(NotificationDeliveryHistory);
  await flushPromises();
  expect(wrapper.text()).toContain("not configured correctly");
  expect(wrapper.text()).toContain("Review and save the provider");
  expect(wrapper.text()).toContain("attempt 1 of 3");
  const retries = wrapper
    .findAll("button")
    .filter((b) => b.text() === "Retry now");
  expect(retries).toHaveLength(1);
  expect(retries[0].attributes("aria-label")).toContain("me");
  await retries[0].trigger("click");
  await flushPromises();
  expect(calls.some((c) => c.url.endsWith("/deliveries/x1/retry"))).toBe(true);
});
