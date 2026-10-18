// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { afterEach, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ProfileAvatar from "../components/ProfileAvatar.vue";
import { currentUser } from "../state/auth";
import type { CurrentUser } from "../services/auth";

const readSource = (path: string) =>
  readFileSync(resolve(process.cwd(), "src", path), "utf8");

afterEach(() => {
  currentUser.value = null;
});

it("shows the picture, or the fallback when there is none or it fails to load", async () => {
  currentUser.value = { id: "u1", profile_picture_version: "3" } as CurrentUser;
  const wrapper = mount(ProfileAvatar, { slots: { default: "AB" } });
  expect(wrapper.find("img").attributes("src")).toContain(
    "/api/user/u1/profile-picture?v=3",
  );
  await wrapper.find("img").trigger("error");
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.text()).toBe("AB");
  currentUser.value = {
    id: "u1",
    profile_picture_version: null,
  } as CurrentUser;
  await wrapper.vm.$nextTick();
  expect(wrapper.text()).toBe("AB");
});

it("keeps every account picture a circle however wide or tall the file is (#239)", () => {
  // A non-square upload squashed the sidebar circle into an oval because the box followed
  // the file's proportions. layout is not computed in the test DOM, so pin the CSS contract.
  const avatar = readSource("components/ProfileAvatar.vue");
  expect(avatar).toMatch(/img\s*{[^}]*aspect-ratio:\s*1\s*\/\s*1/);
  expect(avatar).toMatch(/img\s*{[^}]*object-fit:\s*cover/);
  const sidebar = readSource("components/SidebarNav.vue");
  expect(sidebar).toMatch(/\.nav-avatar\s*{[^}]*aspect-ratio:\s*1\s*\/\s*1/);
  expect(sidebar).toMatch(
    /\.nav-avatar-img\s*{[^}]*aspect-ratio:\s*1\s*\/\s*1/,
  );
});
