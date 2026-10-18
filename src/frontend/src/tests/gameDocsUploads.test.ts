// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import GameMediaPanel from "../components/GameMediaPanel.vue";
import LimitsSection from "../components/settings/LimitsSection.vue";
import { friendlyError } from "../services/apiError";
import { uploadGameFiles } from "../services/media";
import {
  fetchUploadLimitDetails,
  updateUploadLimits,
  type UploadLimitDetails,
} from "../services/settings";

vi.mock("../services/settings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/settings")>()),
  fetchUploadLimitDetails: vi.fn(),
  updateUploadLimits: vi.fn(),
}));

const wrappers: VueWrapper[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

// Ordinary documents, and files a browser may report with no type at all.
const anyFiles = () => [
  new File(["docx"], "Walkthrough.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  }),
  new File(["doc"], "Old manual.doc", { type: "application/msword" }),
  new File(["xlsx"], "Routes.xlsx", { type: "" }),
  new File(["pdf"], "Manual.pdf", { type: "application/pdf" }),
  new File(["exe"], "Tool.exe", { type: "application/x-msdownload" }),
  new File(["license"], "LICENSE", { type: "" }),
];

function panel(kind: "doc" | "screenshot") {
  const wrapper = mount(GameMediaPanel, {
    props: {
      kind,
      gameId: "game",
      items: [],
      trash: [],
      loading: false,
      uploading: false,
      error: null,
    },
    attachTo: document.body,
  });
  wrappers.push(wrapper);
  return wrapper;
}

function pick(wrapper: VueWrapper, files: File[]) {
  const input = wrapper.get<HTMLInputElement>('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    value: files,
    configurable: true,
  });
  return input.trigger("change");
}

it("takes every file type in a game's Docs tab, from the picker and a drop", async () => {
  const wrapper = panel("doc");
  const input = wrapper.get<HTMLInputElement>('input[type="file"]');
  expect(input.element.accept).toBe("*/*");
  expect(wrapper.text()).toContain("files of any kind");

  await pick(wrapper, anyFiles());
  const drop = new Event("drop", { cancelable: true });
  Object.defineProperty(drop, "dataTransfer", {
    value: { types: ["Files"], items: [], files: anyFiles() },
  });
  window.dispatchEvent(drop);

  const batches = wrapper.emitted("files") as File[][][];
  expect(batches.map(([files]) => files.map((file) => file.name))).toEqual([
    anyFiles().map((file) => file.name),
    anyFiles().map((file) => file.name),
  ]);
  expect(wrapper.emitted("problem")).toBeUndefined();
});

it("still keeps documents out of the screenshot gallery", async () => {
  const wrapper = panel("screenshot");
  await pick(wrapper, [
    new File(["png"], "shot.png", { type: "image/png" }),
    ...anyFiles().slice(0, 1),
  ]);
  const [[files]] = wrapper.emitted("files") as File[][][];
  expect(files.map((file) => file.name)).toEqual(["shot.png"]);
  expect(wrapper.emitted("problem")).toEqual([
    ["1 file skipped: this tab takes images only."],
  ]);
});

it("edits the documents cap separately from the image cap", async () => {
  const limits = {
    max_upload_size_mb: 15,
    max_doc_size_mb: 500,
    max_save_archive_size_mb: 4096,
    max_clip_size_mb: 500,
    max_world_save_size_mb: 2000,
  };
  const details = (values: typeof limits) =>
    Object.fromEntries(
      Object.entries(values).map(([key, value]) => [
        key,
        {
          value,
          source: "default",
          locked: false,
          env_name: key.toUpperCase(),
          environment_invalid: false,
        },
      ]),
    ) as UploadLimitDetails;
  vi.mocked(fetchUploadLimitDetails)
    .mockResolvedValueOnce(details(limits))
    .mockResolvedValue(details({ ...limits, max_doc_size_mb: 750 }));
  vi.mocked(updateUploadLimits).mockResolvedValue({
    ...limits,
    max_doc_size_mb: 750,
  });
  const wrapper = mount(LimitsSection, { attachTo: document.body });
  wrappers.push(wrapper);
  await flushPromises();

  const field = wrapper
    .findAll("label")
    .find((label) => label.text() === "Documents (MB)");
  expect(field).toBeDefined();
  const input = wrapper.get<HTMLInputElement>(`#${field!.attributes("for")}`);
  expect(input.element.value).toBe("500");
  expect(wrapper.text()).toContain("MAX_DOC_SIZE_MB");
  await input.setValue("750");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(updateUploadLimits).toHaveBeenCalledWith({
    ...limits,
    max_doc_size_mb: 750,
  });
  expect(input.element.value).toBe("750");
  expect(wrapper.text()).toContain("Limits saved.");
});

it("explains a 413 from a reverse proxy instead of showing its HTML page", async () => {
  const proxyPage =
    "<html><body><h1>413 Request Entity Too Large</h1></body></html>";
  expect(friendlyError(413, proxyPage)).toContain("proxy in front of the app");
  expect(
    friendlyError(
      413,
      JSON.stringify({
        detail: "Sign in to send a request body larger than 1 MB.",
      }),
    ),
  ).toBe("Sign in to send a request body larger than 1 MB.");

  class ProxyRefusal {
    status = 413;
    statusText = "Request Entity Too Large";
    responseText = proxyPage;
    withCredentials = false;
    upload: { onprogress?: unknown } = {};
    onload?: () => void;
    onerror?: () => void;
    open() {}
    send() {
      this.onload?.();
    }
  }
  vi.stubGlobal("XMLHttpRequest", ProxyRefusal);
  const failure = uploadGameFiles("game", "doc", anyFiles().slice(0, 1));
  await expect(failure).rejects.toThrow(
    "Upload failed: The upload is larger than a proxy in front of the app accepts.",
  );
  await failure.catch((error: Error) =>
    expect(error.message).not.toContain("<html>"),
  );
});
