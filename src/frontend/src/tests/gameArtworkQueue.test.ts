// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { cancelQueuedArtwork, queueArtwork } from "../state/gameArtwork";

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

it("starts artwork closest to the viewport centre before top and bottom cards", () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.stubGlobal("innerHeight", 1000);
  const started: number[] = [];
  const elements = Array.from({ length: 10 }, (_, index) => {
    const element = document.createElement("div");
    element.getBoundingClientRect = () =>
      ({ top: index * 100, bottom: index * 100 + 80 }) as DOMRect;
    document.body.appendChild(element);
    queueArtwork(element, () => started.push(index));
    return element;
  });

  expect(frames).toHaveLength(1);
  frames.shift()?.(0);
  expect(started[0]).toBe(5);
  expect(started[1]).toBe(4);
  expect(started).toHaveLength(8);
  frames.shift()?.(16);
  expect(started).toHaveLength(10);
  elements.forEach(cancelQueuedArtwork);
});
