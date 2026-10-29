import type { CopyDisplay } from "../services/identityReview";

/** What a person decided about one suggested pair in the review queue. */
export type ReviewVerdict =
  | {
      action: "same";
      mainId: string;
      display: CopyDisplay;
      sameCopy: boolean;
    }
  | { action: "related"; kind: string; fromId: string }
  | { action: "separate" };
