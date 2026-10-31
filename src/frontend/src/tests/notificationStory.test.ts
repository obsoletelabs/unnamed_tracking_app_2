// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import NotificationStory from "../views/NotificationStory.vue";
import RichNodes from "../components/RichNodes.vue";
import { safeHref } from "../utils/richNodes";
import { closeDialog, activeDialog } from "../state/dialog";

const storyRoute = reactive({ params: { id: "story-1", token: undefined as string | undefined } });
const storyWrappers: ReturnType<typeof mount>[] = [];
vi.mock("vue-router", () => ({
  useRoute: () => storyRoute,
  RouterLink: {
    props: ["to"],
    template: '<a :href="to"><slot /></a>',
  },
}));

const storyBody = {
  story_id: "story-1",
  revision: 2,
  phase: "available",
  status: "Available",
  updated_at: 1_700_000_000,
  event_type: "example.update",
  source: "example.plugin",
  severity: "info",
  rich: true,
  title: "Update ready",
  text: null,
  view: {
    title: "Update ready",
    summary: [
      {
        t: "p",
        c: [
          { t: "text", v: "Version " },
          { t: "strong", c: [{ t: "text", v: "2.0" }] },
          { t: "text", v: " <script>alert(1)</script>" },
        ],
      },
    ],
    preview: "Version 2.0",
    blocks: [
      [
        {
          t: "p",
          c: [
            {
              t: "link",
              href: "javascript:alert(1)",
              c: [{ t: "text", v: "bad link" }],
            },
            { t: "text", v: " " },
            {
              t: "link",
              href: "https://example.com/notes",
              c: [{ t: "text", v: "notes" }],
            },
          ],
        },
      ],
    ],
    facts: [
      { key: "build", label: "Build", kind: "text", text: "7731" },
      {
        key: "at",
        label: "At",
        kind: "timestamp",
        text: "x",
        iso: "2023-11-14T22:13:20Z",
        ts: 1_700_000_000,
      },
    ],
    images: [
      {
        index: 0,
        role: "artwork",
        alt: "Cover",
        state: "proxied",
        url: "/api/notifications/stories/story-1/images/0",
      },
      { index: 1, role: "artwork", alt: "", state: "fallback", url: null },
    ],
    actions: [
      { id: "install", label: "Install now", style: "primary", kind: "action" },
      { id: "wipe", label: "Wipe", style: "danger", kind: "action" },
      {
        id: "notes",
        label: "Notes",
        style: "default",
        kind: "link",
        href: "https://example.com/n",
      },
    ],
    attribution: { label: "Example", url: "https://example.com" },
    expires_at: null,
    stale: false,
  },
};

function respond(routes: Record<string, unknown>) {
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const body = routes[key];
    return Promise.resolve(
      body === undefined
        ? new Response("{}", { status: 404 })
        : new Response(JSON.stringify(body), { status: 200 }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const routes = {
  "GET /api/notifications/stories/story-1": storyBody,
  "GET /api/notifications/stories/story-1/live-links": { items: [] },
  "POST /api/notifications/stories/story-1/actions/install": {
    ok: true,
    message: "Installed 2.0",
  },
  "POST /api/notifications/stories/story-1/actions/wipe": {
    ok: true,
    message: null,
  },
};

async function open() {
  const wrapper = mount(NotificationStory, {
    global: { stubs: { AppTopBar: { template: "<div><slot /></div>" } } },
  });
  storyWrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}

afterEach(() => {
  for (const wrapper of storyWrappers.splice(0)) wrapper.unmount();
  storyRoute.params.id = "story-1";
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("never turns content into markup or unsafe links", async () => {
  respond(routes);
  const wrapper = await open();
  expect(wrapper.find("script").exists()).toBe(false);
  expect(wrapper.text()).toContain("<script>alert(1)</script>");
  const hrefs = wrapper.findAll("a").map((a) => a.attributes("href"));
  expect(hrefs).toContain("https://example.com/notes");
  expect(hrefs.join(" ")).not.toContain("javascript:");
  expect(wrapper.text()).toContain("bad link");
  const external = wrapper.find('a[href="https://example.com/notes"]');
  expect(external.attributes("rel")).toBe("noopener noreferrer nofollow");
});

it("has a screen-reader friendly structure", async () => {
  respond(routes);
  const wrapper = await open();
  expect(wrapper.findAll("h1")).toHaveLength(1);
  expect(wrapper.find("main").attributes("aria-busy")).toBe("false");
  const article = wrapper.find("article");
  expect(article.attributes("aria-labelledby")).toBe("story-title");
  expect(wrapper.find("#story-title").text()).toBe("Update ready");
  expect(wrapper.find("dl dt").text()).toBe("Build");
  expect(wrapper.find("time").attributes("datetime")).toMatch(/Z$/);
  expect(wrapper.find('[role="group"]').attributes("aria-label")).toBe(
    "Actions",
  );
  expect(wrapper.find('[role="status"][aria-live="polite"]').exists()).toBe(
    true,
  );
  expect(wrapper.find("img").attributes("alt")).toBe("Cover");
  const fallback = wrapper.find('svg[role="img"]');
  expect(fallback.attributes("aria-label")).toBe("Image unavailable");
  // every control has an accessible name
  for (const control of wrapper.findAll("button, a, select, input")) {
    const name =
      control.text() ||
      control.attributes("aria-label") ||
      control.element.closest("label")?.textContent;
    expect(name?.trim(), control.html()).toBeTruthy();
  }
});

it("swaps a broken image for fallback artwork", async () => {
  respond(routes);
  const wrapper = await open();
  await wrapper.find("img").trigger("error");
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.findAll('svg[role="img"]')).toHaveLength(2);
});

it("runs an action through the host and announces the result", async () => {
  const fetch = respond(routes);
  const wrapper = await open();
  const install = wrapper
    .findAll("button")
    .find((b) => b.text() === "Install now")!;
  await install.trigger("click");
  await flushPromises();
  expect(wrapper.find('[aria-live="polite"]').text()).toBe("Installed 2.0");
  const call = fetch.mock.calls.find(([url]) =>
    String(url).endsWith("/actions/install"),
  )!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ confirm: false });
});

it("asks before a destructive action and cancels cleanly", async () => {
  const fetch = respond(routes);
  const wrapper = await open();
  const wipe = wrapper.findAll("button").find((b) => b.text() === "Wipe")!;
  await wipe.trigger("click");
  await flushPromises();
  expect(activeDialog.value?.danger).toBe(true);
  closeDialog(false);
  await flushPromises();
  expect(
    fetch.mock.calls.some(([url]) => String(url).endsWith("/actions/wipe")),
  ).toBe(false);
  await wipe.trigger("click");
  await flushPromises();
  closeDialog(true);
  await flushPromises();
  const call = fetch.mock.calls.find(([url]) =>
    String(url).endsWith("/actions/wipe"),
  )!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({ confirm: true });
});

it("shows a neutral message when the story is not available", async () => {
  respond({});
  const wrapper = await open();
  expect(wrapper.find('[role="alert"]').text()).toContain("not available");
  expect(wrapper.find("article").exists()).toBe(false);
});

it("only follows https or application-path links", () => {
  for (const bad of [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,x",
    "http://example.com",
    "//evil.example",
    "https://user:pw@example.com/",
    "/api/admin",
    "/../x",
    "",
    null,
    undefined,
  ])
    expect(safeHref(bad as string)).toBeNull();
  expect(safeHref("https://example.com/a?b=1")).toEqual({
    kind: "external",
    href: "https://example.com/a?b=1",
  });
  expect(safeHref("/settings?section=notifications")?.kind).toBe("route");
});

it("refuses absolute links into the app's own API", () => {
  vi.stubGlobal("location", new URL("https://app.example.com/notifications"));
  try {
    for (const own of [
      "https://app.example.com/api/plugins/demo/frontend/index.html",
      "https://app.example.com/%61pi/plugins/demo/frontend/",
      "https://app.example.com/api",
    ])
      expect(safeHref(own)).toBeNull();
    expect(safeHref("https://app.example.com/games/1")?.kind).toBe("external");
    expect(safeHref("https://other.example.com/api/x")?.kind).toBe("external");
  } finally {
    vi.unstubAllGlobals();
  }
});

it("renders block and inline nodes as text only", () => {
  const wrapper = mount(RichNodes, {
    props: {
      nodes: [
        { t: "h", level: 9, c: [{ t: "text", v: "<b>x</b>" }] },
        { t: "ul", c: [{ t: "li", c: [{ t: "code", v: "<i>y</i>" }] }] },
        { t: "script", v: "alert(1)" },
        { t: "pre", v: "<img src=x onerror=alert(1)>" },
      ],
    },
  });
  expect(wrapper.find("h5").text()).toBe("<b>x</b>");
  expect(wrapper.find("b").exists()).toBe(false);
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.find("script").exists()).toBe(false);
  expect(wrapper.find("li code").text()).toBe("<i>y</i>");
});


it("ignores an old story response after navigating to another story", async () => {
  let resolve!: (response: Response) => void;
  const slow = new Promise<Response>((done) => { resolve = done; });
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url === "/api/notifications/stories/story-1") return slow;
    const body = url.endsWith("live-links") ? { items: [] } : { ...storyBody, story_id: "story-2", title: "Second update" };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }));
  const wrapper = await open();
  storyRoute.params.id = "story-2";
  await flushPromises();
  resolve(new Response(JSON.stringify(storyBody), { status: 200 }));
  await flushPromises();
  expect(wrapper.find("h1").text()).toBe("Second update");
});

it("keeps an old action outcome off the newly opened story", async () => {
  let resolve!: (response: Response) => void;
  const slow = new Promise<Response>((done) => { resolve = done; });
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.endsWith("/actions/install")) return slow;
    const body = url.endsWith("live-links") ? { items: [] } : { ...storyBody, story_id: url.endsWith("story-2") ? "story-2" : "story-1", title: url.endsWith("story-2") ? "Second update" : "First update" };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  }));
  const wrapper = await open();
  await wrapper.findAll("button").find((button) => button.text() === "Install now")!.trigger("click");
  storyRoute.params.id = "story-2";
  await flushPromises();
  resolve(new Response(JSON.stringify({ ok: true, message: "Old action completed" }), { status: 200 }));
  await flushPromises();
  expect(wrapper.find("h1").text()).toBe("Second update");
  expect(wrapper.text()).not.toContain("Old action completed");
});


it("retries an unavailable story and retains content if a later refresh fails", async () => {
  respond({});
  const wrapper = await open();
  respond(routes);
  await wrapper.findAll("button").find((button) => button.text() === "Try again")!.trigger("click");
  await flushPromises();
  expect(wrapper.find("h1").text()).toBe("Update ready");
  respond({});
  await (wrapper.vm as unknown as { load: () => Promise<void> }).load();
  expect(wrapper.find("h1").text()).toBe("Update ready");
  expect(wrapper.find('[role="alert"]').text()).toContain("could not be refreshed");
});
