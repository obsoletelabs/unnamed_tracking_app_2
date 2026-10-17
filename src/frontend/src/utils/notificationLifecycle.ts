import type {
  InboxRetentionPolicy,
  MediaNotification,
  NotificationState,
} from "../services/notifications";

export const STATE_LABEL: Record<NotificationState, string> = {
  unread: "Unread",
  read: "Read",
  dismissed: "Dismissed",
  expired: "Expired",
  deleted: "Deleted",
};

/** Only items still in the active inbox may be read, dismissed or deleted. */
export function isActionable(n: Pick<MediaNotification, "state" | "read">) {
  const state = n.state ?? (n.read ? "read" : "unread");
  return state === "unread" || state === "read";
}

/** Visible expiry: the item's own expiry, else the effective retention window. */
export function expiryOf(
  n: Pick<MediaNotification, "expiresAt" | "eventAt">,
  policy: InboxRetentionPolicy | null,
): number | null {
  if (n.expiresAt) return n.expiresAt;
  if (!policy?.effective_days) return null;
  return n.eventAt + policy.effective_days * 86400;
}

/** Plain-language summary of requested versus effective retention. */
export function retentionSummary(policy: InboxRetentionPolicy): string {
  const days = (value: number) =>
    value ? `${value} day${value === 1 ? "" : "s"}` : "forever";
  const effective = `Notifications are kept for ${days(policy.effective_days)}.`;
  if (policy.limited)
    return `${effective} You asked for ${days(policy.requested_days)}, but this server allows at most ${days(policy.maximum_days)}.`;
  if (policy.inherited)
    return `${effective} This is the server default; change it in notification settings.`;
  return `${effective} This is your own setting.`;
}

export interface LifecycleEntry {
  label: string;
  at: number;
}

/** Ordered timeline from the lifecycle timestamps the server already holds. */
export function lifecycleHistory(
  n: MediaNotification,
  policy: InboxRetentionPolicy | null,
): LifecycleEntry[] {
  const entries: LifecycleEntry[] = [
    { label: "Received", at: n.createdAt ?? n.eventAt },
  ];
  if (n.readAt) entries.push({ label: "Read", at: n.readAt });
  if (n.dismissedAt) entries.push({ label: "Dismissed", at: n.dismissedAt });
  if (n.deletedAt) entries.push({ label: "Deleted", at: n.deletedAt });
  else {
    const expiry = expiryOf(n, policy);
    if (expiry)
      entries.push({
        label: n.state === "expired" ? "Expired" : "Expires",
        at: expiry,
      });
  }
  return entries.sort((a, b) => a.at - b.at);
}
