// Mirrors backend/src/core/auth.py (username_rule_error). Keep the rules and the
// messages identical: the server stays authoritative at save time, this only gives
// the same specific reason while the person is still typing. Whether a name is
// already taken is deliberately not checked here (that would let anyone probe for
// accounts); a conflict is reported by the save request itself.

export const USERNAME_MAX_LENGTH = 100;
export const USERNAME_HELP =
  "Use 1–100 ASCII letters, digits, underscores, hyphens or dots. No spaces.";

const ALLOWED = /^[A-Za-z0-9_.-]$/;
const RULE = "Use only ASCII letters, digits, underscores, hyphens and dots.";

function describeCharacter(char: string): string {
  if (char === " ") return "spaces";
  // Same set Python's str.isprintable() rejects (the backend labels these alike).
  if (/[\p{C}\p{Z}]/u.test(char)) return "control or invisible characters";
  return `“${char}”`;
}

export function usernameRuleError(value: string): string | null {
  const username = value.trim();
  if (!username) return "Enter a username.";
  const characters = Array.from(username);
  if (characters.length > USERNAME_MAX_LENGTH)
    return `Username is too long: use at most ${USERNAME_MAX_LENGTH} characters (this one has ${characters.length}).`;
  const offenders: string[] = [];
  for (const char of characters) {
    if (ALLOWED.test(char)) continue;
    const label = describeCharacter(char);
    if (!offenders.includes(label)) offenders.push(label);
  }
  if (!offenders.length) return null;
  const shown =
    offenders.slice(0, 5).join(", ") + (offenders.length > 5 ? ", …" : "");
  return `Username cannot contain ${shown}. ${RULE}`;
}

// An existing account keeps signing in with its current name even when it breaks
// today's rules, so the unchanged value is never flagged (the server agrees).
export function usernameFieldError(
  value: string,
  unchangedValue?: string | null,
): string | null {
  if (unchangedValue != null && value.trim() === unchangedValue) return null;
  return usernameRuleError(value);
}

const FIELD_MESSAGE =
  /^(Enter a username\.|Username (cannot contain|is too long)|That username is already taken)/;

// Server replies about the username arrive as a sentence (sometimes prefixed with
// the field name by the request validator). Returns it when it belongs at the
// username field, so the form can show it there instead of in a generic banner.
export function usernameServerError(message: string | null): string | null {
  if (!message) return null;
  const text = message.replace(/^Username:\s*(?=Username |Enter )/, "").trim();
  return FIELD_MESSAGE.test(text) ? text : null;
}
