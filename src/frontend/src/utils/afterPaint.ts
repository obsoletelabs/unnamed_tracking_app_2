// Let the mounted route start its critical work before optional badges and banners.
export function afterPaint(callback: () => void): () => void {
  let cancelled = false;
  let frame = 0;
  const run = () => {
    if (!cancelled) callback();
  };
  if (typeof requestAnimationFrame === "function")
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(run);
    });
  else queueMicrotask(run);
  return () => {
    cancelled = true;
    if (frame && typeof cancelAnimationFrame === "function")
      cancelAnimationFrame(frame);
  };
}
