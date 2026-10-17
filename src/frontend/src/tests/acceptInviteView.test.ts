import { afterEach, expect, it, vi } from "vitest";
import { createRenderer, nextTick, reactive, ssrContextKey } from "vue";
import AcceptInvite from "../views/AcceptInvite.vue";
import {
  invitationStatus,
  acceptInvitation,
} from "../services/userInvitations";

const route = reactive({ path: "/accept-invite", hash: "" });
const replace = vi.fn(async (value: { hash: string }) => {
  route.hash = value.hash;
});
vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ replace }),
}));
vi.mock("../services/userInvitations", () => ({
  invitationStatus: vi.fn(),
  acceptInvitation: vi.fn(),
}));
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
interface InviteState {
  username: string;
  password: string;
  confirmation: string;
  credential: { id: string; token: string } | null;
  message: string;
  error: string;
  submit(): Promise<void>;
}
const apps: ReturnType<typeof renderer.createApp>[] = [];
const id = "a134038e-9a2e-4ea1-935c-68a06d631598",
  token = "a".repeat(43);
afterEach(() => {
  apps.splice(0).forEach((app) => app.unmount());
  route.hash = "";
  vi.clearAllMocks();
});
async function mount(fragment: string): Promise<InviteState> {
  route.hash = fragment;
  vi.mocked(invitationStatus).mockResolvedValue(true);
  vi.mocked(acceptInvitation).mockResolvedValue("Account created.");
  const app = renderer.createApp({ ...AcceptInvite, render: () => null });
  app.provide(ssrContextKey, { modules: new Set() });
  apps.push(app);
  app.mount({});
  await nextTick();
  await nextTick();
  return (app._instance as unknown as { setupState: InviteState }).setupState;
}
it("removes the fragment without accepting on page load and clears credentials after POST", async () => {
  const state = await mount(`#id=${id}&token=${token}`);
  expect(route.hash).toBe("");
  expect(state.credential).toEqual({ id, token });
  expect(acceptInvitation).not.toHaveBeenCalled();
  state.username = " invitee ";
  state.password = state.confirmation = "New-Password-789!";
  await state.submit();
  expect(acceptInvitation).toHaveBeenCalledWith(
    id,
    token,
    "invitee",
    "New-Password-789!",
  );
  expect(state.credential).toBeNull();
  expect(state.password).toBe("");
  expect(state.message).toBe("Account created.");
});
it("validates confirmation and accepts a replacement link opened in the same tab", async () => {
  const state = await mount(`#id=${id}&token=${token}`);
  state.username = "invitee";
  state.password = "New-Password-789!";
  state.confirmation = "different";
  await state.submit();
  expect(acceptInvitation).not.toHaveBeenCalled();
  expect(state.error).toBe("The passwords do not match.");
  route.hash = `#id=${id}&token=${"b".repeat(43)}`;
  await nextTick();
  await nextTick();
  expect(state.password).toBe("");
  expect(state.error).toBe("");
  expect(route.hash).toBe("");
  expect(state.credential?.token).toBe("b".repeat(43));
});
it("stops at the username field with the specific reason before contacting the server", async () => {
  const state = await mount(`#id=${id}&token=${token}`);
  state.username = "bad name";
  state.password = state.confirmation = "New-Password-789!";
  await state.submit();
  expect(acceptInvitation).not.toHaveBeenCalled();
  expect(state.error).toBe("");
});
