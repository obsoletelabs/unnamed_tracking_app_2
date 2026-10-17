// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import UsernameField from "../components/UsernameField.vue";

function field(props: Record<string, unknown> = {}) {
  return mount(UsernameField, {
    props: {
      modelValue: "",
      "onUpdate:modelValue": (value: string) =>
        wrapper.setProps({ modelValue: value }),
      ...props,
    },
  });
}
let wrapper: ReturnType<typeof field>;
const error = () => wrapper.find(".username-error").text();

it("stays quiet until the person types, then explains the broken rule", async () => {
  wrapper = field();
  expect(error()).toBe("");
  expect(wrapper.find("input").attributes("aria-invalid")).toBeUndefined();
  await wrapper.find("input").setValue("bad name");
  expect(error()).toContain("Username cannot contain spaces");
  const input = wrapper.find("input");
  expect(input.attributes("aria-invalid")).toBe("true");
  const describedBy = input.attributes("aria-describedby")!.split(" ");
  expect(describedBy).toContain(
    wrapper.find(".username-error").attributes("id"),
  );
  expect(describedBy).toContain(
    wrapper.find(".username-help").attributes("id"),
  );
  expect(wrapper.find(".username-error").attributes("aria-live")).toBe(
    "polite",
  );
  await wrapper.find("input").setValue("good.name");
  expect(error()).toBe("");
  expect(wrapper.find("input").attributes("aria-invalid")).toBeUndefined();
});

it("explains an empty field on blur and when a submit is attempted", async () => {
  wrapper = field();
  await wrapper.find("input").trigger("blur");
  expect(error()).toBe("Enter a username.");
  wrapper = field({ showErrors: true });
  expect(error()).toBe("Enter a username.");
});

it("does not flag the account's existing username", async () => {
  wrapper = field({ modelValue: "Old Name", unchangedValue: "Old Name" });
  await wrapper.find("input").trigger("blur");
  expect(error()).toBe("");
  await wrapper.find("input").setValue("Old Name 2");
  expect(error()).toContain("cannot contain spaces");
});

const taken = {
  message: "That username is already taken. Choose a different username.",
  value: "taken",
};

it("shows a save conflict only while the field holds the submitted text", async () => {
  wrapper = field({ modelValue: "taken", serverError: taken });
  expect(error()).toContain("already taken");
  expect(wrapper.find("input").attributes("aria-invalid")).toBe("true");
  await wrapper.find("input").setValue("taken2");
  expect(error()).toBe("");
});

it("ignores a slow save reply that was for older text", async () => {
  wrapper = field({ modelValue: "taken" });
  await wrapper.find("input").setValue("something-else");
  await wrapper.setProps({ serverError: taken });
  expect(error()).toBe("");
  expect(wrapper.find("input").attributes("aria-invalid")).toBeUndefined();
});
