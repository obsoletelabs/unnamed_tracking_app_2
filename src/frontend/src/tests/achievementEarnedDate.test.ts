// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import AchievementEditDialog from "../components/AchievementEditDialog.vue";
import { localInputToUnixSeconds, toLocalInput } from "../utils/achievements";
import type { Achievement } from "../types/game";

const achievement = (overrides: Partial<Achievement> = {}): Achievement =>
  ({
    id: "a1",
    name: "First Blood",
    unlocked: false,
    unlockedAt: null,
    manualOverride: false,
    ...overrides,
  }) as Achievement;

const stubs = {
  UiModal: { template: "<div><slot /><slot name='footer' /></div>" },
};

function mountDialog(a: Achievement) {
  return mount(AchievementEditDialog, {
    props: { achievement: a, note: "", saving: false, error: "" },
    global: { stubs },
  });
}

describe("earned date helpers", () => {
  it("round-trips a local date and time through unix seconds", () => {
    const date = new Date(2024, 4, 17, 21, 45);
    const input = toLocalInput(date);
    expect(input).toBe("2024-05-17T21:45");
    expect(localInputToUnixSeconds(input)).toBe(
      Math.floor(date.getTime() / 1000),
    );
  });

  it("rejects an empty or invalid value", () => {
    expect(localInputToUnixSeconds("")).toBeNull();
    expect(localInputToUnixSeconds("not a date")).toBeNull();
  });
});

describe("AchievementEditDialog earned date", () => {
  it("only offers the earned date once the achievement is unlocked", async () => {
    const wrapper = mountDialog(achievement());
    expect(wrapper.find('input[type="datetime-local"]').exists()).toBe(false);
    await wrapper.find('input[value="unlocked"]').setValue();
    expect(wrapper.find('input[type="datetime-local"]').exists()).toBe(true);
  });

  it("starts from the existing earned date and saves the chosen one", async () => {
    const earned = new Date(2023, 0, 5, 8, 30);
    const wrapper = mountDialog(
      achievement({
        unlocked: true,
        unlockedAt: earned.toISOString(),
        manualOverride: true,
      }),
    );
    const input = wrapper.find('input[type="datetime-local"]');
    expect((input.element as HTMLInputElement).value).toBe("2023-01-05T08:30");
    await input.setValue("2022-12-31T23:15");
    await wrapper.find("button.ui-btn-primary").trigger("click");
    const [progress, , earnedAt] = wrapper.emitted("save")![0] as [
      string,
      string,
      number | null,
    ];
    expect(progress).toBe("unlocked");
    expect(earnedAt).toBe(
      Math.floor(new Date(2022, 11, 31, 23, 15).getTime() / 1000),
    );
  });

  it("refuses a date in the future", async () => {
    const wrapper = mountDialog(achievement());
    await wrapper.find('input[value="unlocked"]').setValue();
    await wrapper
      .find('input[type="datetime-local"]')
      .setValue(toLocalInput(new Date(Date.now() + 3 * 86_400_000)));
    expect(wrapper.find('[role="alert"]').text()).toContain("future");
    await wrapper.find("button.ui-btn-primary").trigger("click");
    expect(wrapper.emitted("save")).toBeUndefined();
  });

  it("sends no date when the achievement is locked", async () => {
    const wrapper = mountDialog(achievement());
    await wrapper.find('input[value="locked"]').setValue();
    await wrapper.find("button.ui-btn-primary").trigger("click");
    expect(wrapper.emitted("save")![0]).toEqual(["locked", "", null]);
  });
});
