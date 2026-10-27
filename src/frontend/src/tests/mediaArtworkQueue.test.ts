// @vitest-environment happy-dom
import { expect, it, vi } from "vitest";
import { defineComponent, h, withDirectives } from "vue";
import { mount } from "@vue/test-utils";
import { vViewportImage } from "../directives/lazyBackground";

it("starts media posters around the viewport centre and retains them after scrolling away", () => {
  const frames: FrameRequestCallback[] = [];
  let intersect!: IntersectionObserverCallback;
  vi.stubGlobal("innerHeight", 1000);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        intersect = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  const wrapper = mount(
    defineComponent({
      setup: () => () =>
        h(
          "div",
          Array.from({ length: 10 }, (_, index) =>
            withDirectives(h("img"), [
              [vViewportImage, `/poster-${index}.jpg`],
            ]),
          ),
        ),
    }),
    { attachTo: document.body },
  );
  const images = wrapper.findAll("img").map((image, index) => {
    image.element.getBoundingClientRect = () =>
      ({ top: index * 100, bottom: index * 100 + 80 }) as DOMRect;
    return image.element;
  });
  const entry = (
    target: Element,
    isIntersecting: boolean,
  ): IntersectionObserverEntry => ({
    target,
    isIntersecting,
    boundingClientRect: target.getBoundingClientRect(),
    intersectionRect: target.getBoundingClientRect(),
    intersectionRatio: isIntersecting ? 1 : 0,
    rootBounds: null,
    time: 0,
  });
  intersect(
    images.map((target) => entry(target, true)),
    {} as IntersectionObserver,
  );
  frames.shift()?.(0);
  expect(images[5]?.getAttribute("src")).toBe("/poster-5.jpg");
  expect(images[0]?.getAttribute("src")).toBeNull();
  expect(images[9]?.getAttribute("src")).toBeNull();
  frames.shift()?.(16);
  images[5]!.getBoundingClientRect = () =>
    ({ top: -1000, bottom: -920 }) as DOMRect;
  intersect([entry(images[5]!, false)], {} as IntersectionObserver);
  expect(images[5]?.getAttribute("src")).toBe("/poster-5.jpg");
  wrapper.unmount();
  vi.unstubAllGlobals();
});
