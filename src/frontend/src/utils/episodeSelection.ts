// "Which episodes did you watch?" for a manual history entry (#250). A history
// entry only records what happened; it never changes watched progress, so an
// out-of-order evening ("3, 5-7") is stated as exactly that instead of being
// turned into "the first N episodes".

const MAX_EPISODE = 100_000;
const MAX_LISTED = 500;

export interface EpisodeSelection {
  numbers: number[];
  /** The normalised text, for example "3, 5-7". */
  label: string;
}

export function parseEpisodeSelection(
  text: string,
): EpisodeSelection | { error: string } | null {
  const raw = text.trim();
  if (!raw) return null;
  const found = new Set<number>();
  for (const part of raw.split(/[,;\s]+/).filter(Boolean)) {
    const match = /^(\d{1,6})(?:\s*[-–]\s*(\d{1,6}))?$/.exec(part);
    if (!match)
      return { error: `"${part}" is not an episode number or range.` };
    const first = Number(match[1]);
    const last = match[2] === undefined ? first : Number(match[2]);
    if (first < 1 || last < first || last > MAX_EPISODE)
      return { error: `"${part}" is not a valid episode range.` };
    if (last - first + 1 > MAX_LISTED)
      return { error: `Pick at most ${MAX_LISTED} episodes at a time.` };
    for (let n = first; n <= last; n++) found.add(n);
    if (found.size > MAX_LISTED)
      return { error: `Pick at most ${MAX_LISTED} episodes at a time.` };
  }
  return {
    numbers: [...found].sort((a, b) => a - b),
    label: describe([...found]),
  };
}

function describe(values: number[]): string {
  const sorted = [...values].sort((a, b) => a - b);
  const runs: string[] = [];
  for (let i = 0; i < sorted.length;) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    runs.push(j > i ? `${sorted[i]}-${sorted[j]}` : String(sorted[i]));
    i = j + 1;
  }
  return runs.join(", ");
}
