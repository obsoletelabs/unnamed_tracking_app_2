import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import PasswordReset from "../views/PasswordReset.vue";
import {
  completePasswordReset,
  passwordResetStatus,
  requestPasswordReset,
} from "../services/passwordRecovery";

const route = reactive({ path: "/reset-password", hash: "" });
const replace = vi.fn(async (value: { hash: string }) => {
  route.hash = value.hash;
});
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ replace }),
}));
vi.mock("../services/passwordRecovery", async (original) => ({
  ...(await original<typeof import("../services/passwordRecovery")>()),
  passwordResetStatus: vi.fn(),
  requestPasswordReset: vi.fn(),
  completePasswordReset: vi.fn(),
}));

// Follow the repository's Node renderer pattern to exercise the real view state.
const renderer = createRenderer<object, object>({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert: () => {},
  remove: () => {},
  setText: () => {},
  setElementText: () => {},
  patchProp: () => {},
  parentNode: () => null,
  nextSibling: () => null,
});
interface ResetState {
  identifier: string;
  password: string;
  confirmation: string;
  credential: { id: string; token: string } | null;
  finished: boolean;
  message: string;
  error: string;
  submit(): Promise<void>;
}
const apps: ReturnType<typeof renderer.createApp>[] = [];
const id = "a134038e-9a2e-4ea1-935c-68a06d631598";
const token = "a".repeat(43);
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  route.hash = "";
  vi.clearAllMocks();
});
async function mount(fragment = ""): Promise<ResetState> {
  route.hash = fragment;
  vi.mocked(passwordResetStatus).mockResolvedValue(true);
  vi.mocked(requestPasswordReset).mockResolvedValue(
    "If eligible, a link will arrive.",
  );
  vi.mocked(completePasswordReset).mockResolvedValue("Password changed.");
  const app = renderer.createApp({ ...PasswordReset, render: () => null });
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  await nextTick();
  await nextTick();
  return (app._instance as unknown as { setupState: ResetState }).setupState;
}

it("accepts an emailed link in the existing request tab and consumes it only on submit", async () => {
  const state = await mount();
  state.identifier = "person@example.test";
  await state.submit();
  expect(state.finished).toBe(true);
  route.hash = `#id=${id}&token=${token}`;
  await nextTick();
  await nextTick();
  expect(state.finished).toBe(false);
  expect(state.message).toBe("");
  expect(state.credential).toEqual({ id, token });
  expect(route.hash).toBe("");
  expect(replace).toHaveBeenCalledWith({ path: "/reset-password", hash: "" });
  expect(completePasswordReset).not.toHaveBeenCalled();
  state.password = state.confirmation = "Replacement-Password-789!";
  await state.submit();
  expect(completePasswordReset).toHaveBeenCalledWith(
    id,
    token,
    "Replacement-Password-789!",
  );
  expect(state.finished).toBe(true);
  expect(state.credential).toBeNull();
  expect(state.password).toBe("");
});

it("keeps a newly opened link in page memory after removing the address fragment", async () => {
  const state = await mount(`#id=${id}&token=${token}`);
  expect(state.credential).toEqual({ id, token });
  expect(route.hash).toBe("");
  expect(completePasswordReset).not.toHaveBeenCalled();
  state.password = "Replacement-Password-789!";
  state.confirmation = "Different-Password-789!";
  await state.submit();
  expect(state.error).toBe("The passwords do not match.");
  expect(completePasswordReset).not.toHaveBeenCalled();
});
