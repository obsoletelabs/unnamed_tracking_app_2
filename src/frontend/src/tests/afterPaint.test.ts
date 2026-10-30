import { afterEach, expect, it, vi } from "vitest";
import { afterPaint } from "../utils/afterPaint";
afterEach(() => vi.unstubAllGlobals());
it("starts optional work after two animation frames and allows cancellation", () => {
  const frames = new Map<number, FrameRequestCallback>();
  let id = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  const flush = () => {
    const pending = [...frames.values()];
    frames.clear();
    for (const callback of pending) callback(0);
  };
  const task = vi.fn();
  afterPaint(task);
  expect(task).not.toHaveBeenCalled();
  flush();
  expect(task).not.toHaveBeenCalled();
  flush();
  expect(task).toHaveBeenCalledOnce();
  const cancelled = vi.fn();
  const cancel = afterPaint(cancelled);
  flush();
  cancel();
  flush();
  expect(cancelled).not.toHaveBeenCalled();
});
