import { formatDisplayDate } from "./dates";
import { priorityLabel, priorityRank } from "./priority";
import { statusBucket } from "./mediaStatus";

export interface MediaTrackingFact {
  label: string;
  value: string;
}

const DATE_FORMAT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
};

// Priority and the personal start / finish dates, in one shape for the movie,
// TV and anime detail pages. Priority, like for games, only matters while
// there is something left to watch, so it is left out for Completed and
// Dropped titles. Dates use the shared calendar-day formatting.
export function mediaTrackingFacts(media: {
  status: string;
  priority: string | null;
  startDate: string | null;
  endDate: string | null;
}): MediaTrackingFact[] {
  const facts: MediaTrackingFact[] = [];
  const bucket = statusBucket(media.status);
  const rank = priorityRank(media.priority);
  if (rank !== null && bucket !== "completed" && bucket !== "dropped")
    facts.push({ label: "Priority", value: priorityLabel(rank) });
  if (media.startDate)
    facts.push({
      label: "Started",
      value: formatDisplayDate(media.startDate, DATE_FORMAT),
    });
  if (media.endDate)
    facts.push({
      label: "Finished",
      value: formatDisplayDate(media.endDate, DATE_FORMAT),
    });
  return facts;
}
