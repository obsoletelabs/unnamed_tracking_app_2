// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { expect, it, vi } from "vitest";
import { useMediaQuickSave } from "../composables/useMediaQuickSave";
import MyNote from "../components/MyNote.vue";

interface Row {
  id: string;
  favorite: boolean;
  note: string | null;
  rating: number | null;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const row = (extra: Partial<Row> = {}): Row => ({
  id: "a",
  favorite: false,
  note: null,
  rating: null,
  ...extra,
});

it("sends one save at a time and undoes only the change that failed", async () => {
  const record = ref<Row | null>(row());
  const first = deferred<Row>();
  const bodies: Row[] = [];
  const persist = vi.fn(async (body: Row) => {
    bodies.push({ ...body });
    if (bodies.length === 1) return first.promise;
    throw new Error("Down");
  });
  const { quickSave, saveError } = useMediaQuickSave(record, persist);
  const favourite = quickSave((r) => (r.favorite = true), "fav failed");
  const rating = quickSave((r) => (r.rating = 4), "rating failed");
  expect(record.value).toMatchObject({ favorite: true, rating: 4 });
  await flushPromises();
  expect(persist).toHaveBeenCalledTimes(1);
  first.resolve(row({ favorite: true }));
  expect(await favourite).toBe(true);
  expect(await rating).toBe(false);
  // The second save was the accepted record plus its own change only.
  expect(bodies[1]).toMatchObject({ favorite: true, rating: 4 });
  expect(record.value).toMatchObject({ favorite: true, rating: null });
  expect(saveError.value).toBe("rating failed");
});

it("keeps a late answer off a title the page has left", async () => {
  const record = ref<Row | null>(row());
  const slow = deferred<Row>();
  const { quickSave, saveError } = useMediaQuickSave(
    record,
    () => slow.promise,
  );
  const pending = quickSave((r) => (r.favorite = true), "failed");
  record.value = row({ id: "b" });
  slow.reject(new Error("Down"));
  await pending;
  expect(record.value).toEqual(row({ id: "b" }));
  expect(saveError.value).toBeNull();
});

it("keeps the note editor and its text open when the save fails", async () => {
  let accept = false;
  const saveNote = vi.fn(async () => accept);
  const wrapper = mount(MyNote, { props: { note: null, saveNote } });
  await wrapper.find("button").trigger("click"); // Add a note
  await wrapper.find("textarea").setValue("Remember the ending");
  await wrapper.find(".btn-solid").trigger("click");
  await flushPromises();
  expect(saveNote).toHaveBeenCalledWith("Remember the ending");
  expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe(
    "Remember the ending",
  );
  expect(wrapper.find("[role=alert]").text()).toContain("could not be saved");
  accept = true;
  await wrapper.find(".btn-solid").trigger("click");
  await flushPromises();
  expect(wrapper.find("textarea").exists()).toBe(false);
});
