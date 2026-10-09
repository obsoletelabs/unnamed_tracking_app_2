import { expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { createMemoryHistory, createRouter } from "vue-router";
import SidebarNav from "../components/SidebarNav.vue";

it.each([
  ["/games", "/games"],
  ["/games/title-id", "/games"],
  ["/games/collections", "/games/collections"],
  ["/games/collections/collection-id", "/games/collections"],
])(
  "highlights only the specific sidebar entry for %s",
  async (path, expected) => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/:pathMatch(.*)*", component: { render: () => null } }],
    });
    await router.push(path);
    const app = createSSRApp(SidebarNav);
    app.use(router);
    const html = await renderToString(app);
    const entries = [...html.matchAll(/<a\b[^>]*>/g)].map(([tag]) => ({
      path: tag.match(/href="([^"]+)"/)?.[1],
      classes: tag.match(/class="([^"]+)"/)?.[1].split(/\s+/) ?? [],
      current: tag.includes('aria-current="page"'),
    }));
    const active = entries.filter(
      (entry) =>
        entry.classes.includes("nav-item") && entry.classes.includes("active"),
    );
    expect(active.map((entry) => entry.path)).toEqual([expected]);
    expect(active.every((entry) => entry.current)).toBe(true);
  },
);
