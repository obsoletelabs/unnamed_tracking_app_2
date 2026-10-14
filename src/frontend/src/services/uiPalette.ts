export type PaletteId = "orange" | "green" | "classic" | "custom";
export type PaletteMode = "light" | "dark";
export type PresentationStyle = "archive-pocket" | "classic";
export const PALETTE_FIELDS = [
  ["background", "Page background"],
  ["surface", "Cards & menus"],
  ["surface_alt", "Secondary surfaces"],
  ["text", "Main text"],
  ["muted", "Secondary text"],
  ["accent", "Accent & selected controls"],
  ["success", "Success"],
  ["warning", "Warning"],
  ["error", "Error"],
  ["info", "Information"],
  ["purple", "Planning & special status"],
] as const;
export type PaletteRole = (typeof PALETTE_FIELDS)[number][0];
export type PaletteColors = Record<PaletteRole, string>;
export type CustomPalette = Partial<Record<PaletteMode, PaletteColors>>;

export const ORANGE_PALETTE: Record<PaletteMode, PaletteColors> = {
  light: {
    background: "#f5f4f1",
    surface: "#ffffff",
    surface_alt: "#eeece7",
    text: "#252a30",
    muted: "#59616c",
    accent: "#a4520d",
    success: "#216e3e",
    warning: "#855000",
    error: "#b42318",
    info: "#265a8b",
    purple: "#6951a2",
  },
  dark: {
    background: "#17191c",
    surface: "#212428",
    surface_alt: "#2b2f34",
    text: "#edf0f3",
    muted: "#adb5c0",
    accent: "#efac59",
    success: "#96d5a9",
    warning: "#f2c87a",
    error: "#ffa6a0",
    info: "#a2c8ef",
    purple: "#c6b5f1",
  },
};
const GREEN_PALETTE: Record<PaletteMode, PaletteColors> = {
  light: {
    ...ORANGE_PALETTE.light,
    background: "#f1f5f1",
    surface_alt: "#e7eee7",
    text: "#203128",
    muted: "#52665a",
    accent: "#276641",
  },
  dark: {
    ...ORANGE_PALETTE.dark,
    background: "#141c18",
    surface: "#1d2922",
    surface_alt: "#29382f",
    text: "#e8f2eb",
    muted: "#a9bdb0",
    accent: "#8dd4a6",
  },
};

// Rosefall PR #469 uses near-black panels and orange controls. Light mode
// preserves the same contrast hierarchy; it is an adaptation, not an old UI.
export const CLASSIC_PALETTE: Record<PaletteMode, PaletteColors> = {
  light: {
    background: "#f4f3f0",
    surface: "#ffffff",
    surface_alt: "#eae8e3",
    text: "#202020",
    muted: "#5d5d5d",
    accent: "#95560a",
    success: "#26733c",
    warning: "#855000",
    error: "#b42318",
    info: "#265a8b",
    purple: "#6951a2",
  },
  dark: {
    background: "#0d0d0d",
    surface: "#1a1a1a",
    surface_alt: "#222222",
    text: "#f2f2f2",
    muted: "#9c9c9c",
    accent: "#d68a34",
    success: "#86c99a",
    warning: "#e9a94f",
    error: "#ef9999",
    info: "#93bde6",
    purple: "#c084fc",
  },
};

export function contrastRatio(first: string, second: string): number {
  const luminance = (hex: string) => {
    const values = [1, 3, 5]
      .map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255)
      .map((value) =>
        value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
      );
    return values[0]! * 0.2126 + values[1]! * 0.7152 + values[2]! * 0.0722;
  };
  const values = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (values[1]! + 0.05) / (values[0]! + 0.05);
}

export function paletteContrastIssues(colors: PaletteColors): string[] {
  const issues: string[] = [];
  for (const background of ["background", "surface", "surface_alt"] as const) {
    for (const role of [
      "text",
      "muted",
      "accent",
      "success",
      "warning",
      "error",
      "info",
      "purple",
    ] as const) {
      if (contrastRatio(colors[role], colors[background]) < 4.5)
        issues.push(
          `${PALETTE_FIELDS.find((field) => field[0] === role)![1]} on ${PALETTE_FIELDS.find((field) => field[0] === background)![1]}`,
        );
    }
  }
  return issues;
}

export function paletteColors(
  id: PaletteId,
  mode: PaletteMode,
  custom: CustomPalette = {},
  style: PresentationStyle = "archive-pocket",
): PaletteColors {
  return id === "green"
    ? GREEN_PALETTE[mode]
    : id === "custom"
      ? (custom[mode] ?? ORANGE_PALETTE[mode])
      : id === "classic" || style === "classic"
        ? CLASSIC_PALETTE[mode]
        : ORANGE_PALETTE[mode];
}

export function paletteTokens(
  id: PaletteId,
  mode: PaletteMode,
  custom: CustomPalette = {},
  style: PresentationStyle = "archive-pocket",
): Record<string, string> {
  const colors = paletteColors(id, mode, custom, style);
  const mix = (color: string, percent: number, background = colors.surface) =>
    `color-mix(in srgb, ${color} ${percent}%, ${background})`;
  const onAccent =
    contrastRatio(colors.accent, "#ffffff") >=
    contrastRatio(colors.accent, "#000000")
      ? "#ffffff"
      : "#000000";
  const originalDark =
    mode === "dark" &&
    (id === "classic" || (style === "classic" && id === "orange"));
  return {
    "--ui-bg": colors.background,
    "--ui-surface": colors.surface,
    "--ui-surface-2": colors.surface_alt,
    "--ui-popover": colors.surface,
    "--ui-text": colors.text,
    "--ui-dim": colors.muted,
    "--ui-faint": colors.muted,
    "--ui-border-soft": originalDark ? "#222222" : mix(colors.text, 12),
    "--ui-border": originalDark ? "#2b2b2b" : mix(colors.text, 22),
    "--ui-border-strong": mix(colors.text, 45),
    "--ui-accent": colors.accent,
    "--ui-accent-text": colors.accent,
    "--ui-accent-soft": mix(colors.accent, 12),
    "--ui-accent-line": mix(colors.accent, 60),
    "--ui-on-accent": onAccent,
    "--ui-error": colors.error,
    "--ui-danger":
      contrastRatio(colors.error, "#ffffff") >= 4.5
        ? colors.error
        : "color-mix(in srgb, " + colors.error + " 35%, #000000)",
    "--ui-danger-soft": mix(colors.error, 12),
    ...Object.fromEntries(
      (["success", "warning", "info", "purple"] as const).flatMap((role) => {
        const token = role === "success" ? "good" : role;
        return [
          [`--ui-${token}`, colors[role]],
          [`--ui-${token}-soft`, mix(colors[role], 12)],
        ];
      }),
    ),
  };
}

export function applyPalette(
  root: HTMLElement,
  id: PaletteId,
  mode: PaletteMode,
  custom: CustomPalette,
  highContrast = false,
  style: PresentationStyle = "archive-pocket",
): void {
  // Remove only this palette's owned properties; layout and plugin styles remain separate.
  const tokens = paletteTokens(id, mode, custom, style);
  if (highContrast) {
    tokens["--ui-dim"] = tokens["--ui-text"]!;
    tokens["--ui-faint"] = tokens["--ui-text"]!;
    tokens["--ui-border"] = tokens["--ui-border-strong"]!;
  }
  for (const token of Object.keys(tokens)) root.style.removeProperty(token);
  root.dataset.palette = id;
  if (id !== "orange" || style === "classic")
    for (const [token, value] of Object.entries(tokens))
      root.style.setProperty(token, value);
}
