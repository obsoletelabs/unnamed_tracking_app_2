// The single MAL-style 5-value status set every media surface shows to
// the user (List/Shelf/Board tabs, status dropdowns on the create/edit
// forms and detail pages). The real backend enum still has 8 values
// (wishlist/watchlist/backlog/in progress/watched/favorite/rewatch/
// dropped) since `favorite` and `rewatch` distinctions are useful to keep
// on the row itself — but nothing in the UI should ever present all 8 as
// separate choices. Reads collapse into these 5 buckets; writes expand
// back to one canonical real value per bucket.
export const STATUS_BUCKETS = [
  { key: "watching", label: "Watching" },
  { key: "completed", label: "Completed" },
  { key: "hold", label: "On Hold" },
  { key: "dropped", label: "Dropped" },
  { key: "plan", label: "Plan to Watch" },
] as const;

export type StatusBucketKey = (typeof STATUS_BUCKETS)[number]["key"];

const REAL_TO_BUCKET: Record<string, StatusBucketKey> = {
  wishlist: "plan",
  watchlist: "plan",
  backlog: "hold",
  "in progress": "watching",
  watched: "completed",
  favorite: "completed",
  rewatch: "watching",
  dropped: "dropped",
};

const BUCKET_TO_REAL: Record<StatusBucketKey, string> = {
  plan: "wishlist",
  hold: "backlog",
  watching: "in progress",
  completed: "watched",
  dropped: "dropped",
};

export function statusBucket(realStatus: string): StatusBucketKey {
  return REAL_TO_BUCKET[realStatus] ?? "plan";
}

export function bucketToReal(bucket: string): string {
  return BUCKET_TO_REAL[bucket as StatusBucketKey] ?? bucket;
}

export function statusBucketLabel(realStatus: string): string {
  const bucket = statusBucket(realStatus);
  return STATUS_BUCKETS.find((s) => s.key === bucket)?.label ?? "Plan to Watch";
}

export function mediaBucketCounts(
  realCounts: Record<string, number>,
): Record<string, number> {
  const counts: Record<string, number> = {
    all: 0,
    ...Object.fromEntries(STATUS_BUCKETS.map(({ key }) => [key, 0])),
  };
  for (const [status, count] of Object.entries(realCounts)) {
    const bucket = REAL_TO_BUCKET[status.toLowerCase().replaceAll("_", " ")];
    if (!bucket || !Number.isFinite(count) || count < 0) continue;
    counts[bucket] += count;
    counts.all += count;
  }
  return counts;
}
