// @vitest-environment happy-dom

import { mount, flushPromises } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import AdminSection from "../components/settings/AdminSection.vue";
import source from "../components/settings/AdminSection.vue?raw";

vi.mock("../services/admin", () => ({
  listUsers: vi.fn().mockResolvedValue([
    {
      id: 1,
      username: "ada.l",
      display_name: "Ada Lovelace",
      email: "ada@example.test",
      is_admin: false,
      created_at: "2026-01-01T00:00:00Z",
    },
  ]),
  createUser: vi.fn(),
  deleteUser: vi.fn(),
  setUserAdmin: vi.fn(),
}));
vi.mock("../services/adminUsers", () => ({
  listInvitations: vi.fn().mockResolvedValue([]),
}));
vi.mock("../services/passwordPolicy", () => ({
  fetchPasswordPolicy: vi.fn().mockResolvedValue({}),
  passwordValidationErrors: vi.fn().mockReturnValue([]),
}));

describe("admin users card", () => {
  it("keeps the label separate from the username and display name value", async () => {
    const wrapper = mount(AdminSection, {
      global: {
        stubs: { PluginAdminActions: true, PluginExtensionSlot: true },
      },
    });
    await flushPromises();
    const cell = wrapper.find('td[data-label="Sign-in username"]');
    expect(cell.exists()).toBe(true);
    const value = cell.find(".cell-value");
    expect(value.text()).toContain("ada.l");
    expect(value.text()).toContain("Display name: Ada Lovelace");
    expect(cell.text()).not.toContain("Sign-in username");
  });

  it("lets the phone card label wrap instead of overflowing into the value", () => {
    const rule = source.match(/td\[data-label\]::before \{[^}]*\}/)?.[0] ?? "";
    expect(rule).toContain("white-space: normal");
    expect(rule).not.toContain("white-space: nowrap");
    expect(rule).toMatch(/flex: 0 0 \d+px/);
  });
});
