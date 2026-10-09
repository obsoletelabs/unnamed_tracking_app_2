import { failedRequest } from "./apiError";

export interface ProxyPreset {
  label: string;
  values: string[];
}

export async function fetchProxyPresets(): Promise<
  Record<string, ProxyPreset>
> {
  const response = await fetch("/api/internal/real-ip/presets", {
    credentials: "include",
  });
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()).presets;
}

export async function validateProxyEntries(value: string): Promise<string> {
  const response = await fetch("/api/internal/real-ip/validate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()).value;
}
