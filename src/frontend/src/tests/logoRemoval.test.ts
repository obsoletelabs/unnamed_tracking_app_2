// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import AppBrand from "../components/AppBrand.vue";
import ProfileAvatar from "../components/ProfileAvatar.vue";
import { applyBranding, branding } from "../state/branding";
import { currentUser } from "../state/auth";
import { removeProfilePicture } from "../services/auth";
import { fetchBranding } from "../services/branding";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("app brand shows the logo, then the default mark once it is removed or missing", async () => {
  const previous = branding.value;
  try {
    applyBranding({ ...previous, logo_url: "/api/branding/assets/logo/a.png" });
    const wrapper = mount(AppBrand);
    expect(wrapper.find("img").exists()).toBe(true);
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("svg").exists()).toBe(true);
    applyBranding({ ...previous, logo_url: "/api/branding/assets/logo/b.png" });
    await nextTick();
    expect(wrapper.find("img").exists()).toBe(true);
    applyBranding({ ...previous, logo_url: null });
    await nextTick();
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("svg").exists()).toBe(true);
  } finally {
    applyBranding(previous);
  }
});

it("profile avatar falls back to initials when removed or unloadable", async () => {
  const user = {
    id: "u1",
    username: "ab",
    email: "a@b.c",
    is_admin: false,
    profile_picture_version: "v1",
  };
  currentUser.value = user as never;
  const wrapper = mount(ProfileAvatar, { slots: { default: "AB" } });
  expect(wrapper.find("img").attributes("src")).toContain("v=v1");
  await wrapper.find("img").trigger("error");
  expect(wrapper.text()).toBe("AB");
  currentUser.value = { ...user, profile_picture_version: null } as never;
  await nextTick();
  expect(wrapper.find("img").exists()).toBe(false);
  currentUser.value = null;
});

it("removal requests use DELETE and branding reads bypass the HTTP cache", async () => {
  const fetchMock = vi.fn(async () =>
    Response.json({ profile_picture_version: null }),
  );
  vi.stubGlobal("fetch", fetchMock);
  await expect(removeProfilePicture("u1")).resolves.toBeNull();
  expect(fetchMock).toHaveBeenCalledWith("/api/user/u1/profile-picture", {
    method: "DELETE",
    credentials: "include",
  });
  await fetchBranding();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/branding",
    expect.objectContaining({ cache: "no-store" }),
  );
  fetchMock.mockResolvedValueOnce(
    Response.json({ detail: "Nope" }, { status: 500 }),
  );
  await expect(removeProfilePicture("u1")).rejects.toThrow("Nope");
});
