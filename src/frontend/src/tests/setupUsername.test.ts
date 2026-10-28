// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import Setup from "../views/Setup.vue";
import { createInitialAdmin } from "../services/setup";

const replace = vi.fn();
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace }),
}));
vi.mock("../state/auth", () => ({
  currentUser: { value: null },
  checkAuth: vi.fn(),
}));
vi.mock("../services/passwordPolicy", () => ({
  fetchPasswordPolicy: vi.fn(async () => null),
  passwordValidationErrors: vi.fn(() => []),
}));
vi.mock("../services/setup", () => {
  const field = (name: string, type: string, value: unknown) => ({
    name,
    label: name,
    type,
    choices: [],
    description: "",
    hint: "",
    placeholder: "",
    required: true,
    required_group: null,
    heading: null,
    secret: type === "secret",
    generated: false,
    deprecated: false,
    deprecated_message: "",
    visible: true,
    env_only: false,
    locked: false,
    configured: false,
    source: "unset",
    value,
  });
  const section = {
    id: "first_admin",
    title: "First administrator",
    description: "",
    menu: null,
    required: true,
    removable: false,
    visible: true,
    default: true,
    status: "missing",
    blocked: false,
    blocked_message: "",
    env_configured: false,
    fields: [
      field("PRIMARY_USER_USERNAME", "text", ""),
      field("PRIMARY_USER_EMAIL", "email", "a@example.com"),
      field("PRIMARY_USER_PASSWORD", "secret", "pw"),
    ],
  };
  return {
    fetchSetupStatus: vi.fn(async () => ({
      setup_required: true,
      startup_mode: "production",
      startup_ui_enabled: true,
    })),
    fetchSetupConfiguration: vi.fn(async () => ({
      sections: [section],
      startup_mode: "production",
      startup_ui_enabled: true,
    })),
    saveSetupConfiguration: vi.fn(),
    createInitialAdmin: vi.fn(async () => undefined),
  };
});

async function openAdminStep() {
  const wrapper = mount(Setup, {
    global: { stubs: { TrustedProxyControls: true, AppBrand: true } },
  });
  await flushPromises();
  await wrapper
    .findAll("button.nav-item")
    .find((b) => b.text().includes("First administrator"))!
    .trigger("click");
  return wrapper;
}

it("explains a bad first-admin username at the field and blocks finishing", async () => {
  const wrapper = await openAdminStep();
  const usernameInput = wrapper.find("input");
  const message = () => wrapper.find("#PRIMARY_USER_USERNAME-error");
  expect(message().text()).toBe("");
  await usernameInput.setValue("bad name");
  expect(message().text()).toContain("Username cannot contain spaces");
  expect(usernameInput.attributes("aria-invalid")).toBe("true");
  expect(usernameInput.attributes("aria-describedby")).toBe(
    "PRIMARY_USER_USERNAME-error",
  );

  await wrapper.find("button.primary").trigger("click");
  await flushPromises();
  expect(createInitialAdmin).not.toHaveBeenCalled();

  await usernameInput.setValue("good.name");
  expect(message().text()).toBe("");
  expect(usernameInput.attributes("aria-invalid")).toBeUndefined();
});

it("requires the first administrator password to be confirmed", async () => {
  const wrapper = await openAdminStep();
  await wrapper.find("input[type='text']").setValue("admin.one");
  await wrapper.findAll("input[type='password']").at(0)!.setValue("pw");

  await wrapper.find("button.primary").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Administrator passwords do not match");
  expect(createInitialAdmin).not.toHaveBeenCalled();

  await wrapper.findAll("input[type='password']").at(-1)!.setValue("pw");
  await wrapper.find("button.primary").trigger("click");
  await flushPromises();
  expect(createInitialAdmin).toHaveBeenCalledOnce();
});
