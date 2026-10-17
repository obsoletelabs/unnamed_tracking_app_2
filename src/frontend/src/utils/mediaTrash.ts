// Wording and dates for the movie, TV and anime trash (Settings > Library >
// Trash): what emptying a kind's trash did, and when opt-in retention deletes
// a trashed title for good. Kept apart from the component so the exact
// sentences people rely on are tested directly.
import type { EmptyTrashResult, TrashedMedia } from "../services/mediaApi";
import type { Preferences } from "../services/preferences";

export type RetentionDays = Preferences["media_trash_retention_days"];

export const RETENTION_CHOICES: { value: RetentionDays; label: string }[] = [
  { value: 0, label: "Never" },
  { value: 7, label: "After 7 days" },
  { value: 14, label: "After 14 days" },
  { value: 30, label: "After 30 days" },
  { value: 90, label: "After 90 days" },
];

const DAY_SECONDS = 24 * 60 * 60;

export interface TrashNoun {
  one: string;
  many: string;
}

export function countOf(count: number, noun: TrashNoun): string {
  return `${count} ${count === 1 ? noun.one : noun.many}`;
}

// The newest deletion time shown, which bounds what Empty trash may delete.
export function newestDeletion(rows: TrashedMedia[]): number {
  return rows.reduce((newest, row) => Math.max(newest, row.deleted_at), 0);
}

// When retention deletes a title: its deletion time plus the chosen period.
export function retentionDeadline(
  deletedAt: number,
  days: RetentionDays,
): number | null {
  return days ? deletedAt + days * DAY_SECONDS : null;
}

// Titles a period would delete at the next clean-up, because they are
// already older than it.
export function expiredUnder(
  rows: TrashedMedia[],
  days: RetentionDays,
  nowSeconds: number,
): number {
  if (!days) return 0;
  return rows.filter((row) => row.deleted_at <= nowSeconds - days * DAY_SECONDS)
    .length;
}

function titles(items: TrashedMedia[]): string {
  const shown = items.slice(0, 5).map((item) => `"${item.title}"`);
  const more = items.length - shown.length;
  return more > 0 ? `${shown.join(", ")} and ${more} more` : shown.join(", ");
}

// One sentence for what happened, including anything left behind, so a
// partial result is never mistaken for success.
export function emptyTrashMessage(
  result: EmptyTrashResult,
  noun: TrashNoun,
  label: string,
): string {
  if (result.error) {
    const reason = result.error.replace(/[.\s]+$/, "");
    return result.deleted
      ? `Deleted ${countOf(result.deleted, noun)} permanently, then emptying the ${label} trash stopped: ${reason}. The rest are still in the trash; try again.`
      : `Could not empty the ${label} trash: ${reason}. Nothing was deleted; try again.`;
  }
  const failed = result.failedItems.length;
  const unprocessed = result.remaining - failed;
  if (!result.deleted && !failed && unprocessed <= 0)
    return "Nothing was deleted: those titles had already been restored or deleted.";
  const parts = [
    result.deleted
      ? `Deleted ${countOf(result.deleted, noun)} permanently.`
      : "Nothing was deleted.",
  ];
  if (failed) {
    parts.push(
      `${countOf(failed, noun)} could not be deleted and ${failed === 1 ? "is" : "are"} still in the trash: ${titles(result.failedItems)}. Try again, or delete ${failed === 1 ? "it" : "them"} one at a time.`,
    );
  }
  if (unprocessed > 0) {
    parts.push(
      `${countOf(unprocessed, noun)} ${unprocessed === 1 ? "is" : "are"} still in the trash; empty it again to continue.`,
    );
  }
  return parts.join(" ");
}
