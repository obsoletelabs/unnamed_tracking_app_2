import { afterEach, expect, it } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { createMemoryHistory, createRouter } from "vue-router";
import ProfileMenu from "../components/ProfileMenu.vue";
import ProfileSection from "../components/settings/ProfileSection.vue";
import { currentUser } from "../state/auth";

const user = {
  id: "avatar-user",
  username: "Member",
  email: "member@example.invalid",
  is_admin: false,
  steamgriddb_api_key: null,
};
afterEach(() => (currentUser.value = null));

async function render(menu = true) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div />" } }],
  });
  await router.push("/");
  const app = menu
    ? createSSRApp(ProfileMenu, { initials: "ME" })
    : createSSRApp(ProfileSection);
  app.use(router);
  return renderToString(app);
}

it("renders initials without requesting an absent picture in either profile surface", async () => {
  currentUser.value = { ...user, profile_picture_version: null };
  for (const menu of [true, false]) {
    const html = await render(menu);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("/profile-picture");
    expect(html).toContain("ME");
  }
});

it("reuses the stored version URL across renders and changes it after upload or account switch", async () => {
  currentUser.value = { ...user, profile_picture_version: "123-45" };
  for (const menu of [true, false]) {
    const first = await render(menu);
    expect(first).toContain("/api/user/avatar-user/profile-picture?v=123-45");
    expect(await render(menu)).toEqual(first);
  }
  currentUser.value = { ...user, profile_picture_version: "124-45" };
  expect(await render()).toContain("/profile-picture?v=124-45");
  currentUser.value = {
    ...user,
    id: "other-user",
    profile_picture_version: null,
  };
  expect(await render()).not.toContain("<img");
});
