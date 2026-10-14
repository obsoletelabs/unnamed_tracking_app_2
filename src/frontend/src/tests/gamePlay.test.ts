// @vitest-environment happy-dom
import { mount, flushPromises } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import GamePlayFields from "../components/game/GamePlayFields.vue";
import BulkEditModal from "../components/BulkEditModal.vue";
import { bulkUpdateGames, setGamePlayCompatibility } from "../services/games";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("multiplayer presets", () => {
  it("allows marking multiplayer without inventing player limits", async () => {
    const wrapper = mount(GamePlayFields, { props: { modelValue: null } });
    await wrapper.get('input[type="checkbox"]').setValue(true);
    const marker = {
      minPlayers: null,
      maxPlayers: null,
      durationMinutes: null,
      crossplay: false,
    };
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([marker]);
    await wrapper.setProps({ modelValue: marker });
    expect(wrapper.text()).not.toContain("Minimum players");
    await wrapper.findAll('input[type="checkbox"]')[1]!.setValue(true);
    expect(wrapper.emitted("update:modelValue")?.[1]).toEqual([
      { ...marker, minPlayers: 1, maxPlayers: 4 },
    ]);
  });

  it("bulk editing sends only opted-in fields and can explicitly clear a preset", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(
        async () => new Response(JSON.stringify({ updated: 2 })),
      );
    vi.stubGlobal("fetch", fetch);
    vi.stubEnv("VITE_USE_MOCK_DATA", "false");
    const wrapper = mount(BulkEditModal, {
      props: { gameIds: ["one", "two"] },
    });
    const check = wrapper
      .findAll("label")
      .find((label) => label.text() === "Apply multiplayer settings");
    expect(check).toBeDefined();
    await check!.get('input[type="checkbox"]').setValue(true);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({
      game_ids: ["one", "two"],
      play_compatibility: null,
    });
    expect(wrapper.emitted("saved")).toEqual([[2]]);
    await bulkUpdateGames(["one"], { favorite: true });
    expect(JSON.parse(fetch.mock.calls[1]![1].body)).toEqual({
      game_ids: ["one"],
      favorite: true,
    });
  });

  it("updates only multiplayer details on the single-game page", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 403 }));
    vi.stubGlobal("fetch", fetch);
    vi.stubEnv("VITE_USE_MOCK_DATA", "false");
    await expect(
      setGamePlayCompatibility("one", {
        minPlayers: null,
        maxPlayers: null,
        durationMinutes: null,
        crossplay: false,
      }),
    ).rejects.toThrow();
    expect(fetch.mock.calls[0]![0]).toBe("/api/game/update/one");
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({
      play_compatibility: {
        min_players: null,
        max_players: null,
        duration_minutes: null,
        crossplay: false,
      },
    });
  });
});
