interface NumberLimits {
  min?: number;
  max?: number;
  integer?: boolean;
}

/** Vue number inputs return an empty string when cleared, even with .number. */
export function numberOrNull(
  value: unknown,
  label = "Value",
  limits: NumberLimits = {},
): number | null {
  if (value == null || (typeof value === "string" && !value.trim()))
    return null;
  const number =
    typeof value === "number" || typeof value === "string"
      ? Number(value)
      : NaN;
  if (!Number.isFinite(number))
    throw new Error(`${label}: enter a finite number.`);
  if (limits.integer && !Number.isInteger(number))
    throw new Error(`${label}: enter a whole number.`);
  if (limits.min !== undefined && number < limits.min)
    throw new Error(`${label}: enter ${limits.min} or more.`);
  if (limits.max !== undefined && number > limits.max)
    throw new Error(`${label}: enter ${limits.max} or less.`);
  return number;
}

export function requiredNumber(
  value: unknown,
  label: string,
  limits: NumberLimits = {},
): number {
  const number = numberOrNull(value, label, limits);
  if (number === null) throw new Error(`${label}: enter a number.`);
  return number;
}
