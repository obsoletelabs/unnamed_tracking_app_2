import type { PluginStatus, PluginSummary } from "../services/plugins";

// How the Plugin Manager presents a plugin's runtime state and publisher
// trust: one readable word each, and a tone the card colours it by, so a
// failed or unsigned plugin stands out from the healthy ones at a glance.

export type StatusTone = "good" | "warn" | "danger" | "neutral";

const STATUS_LABELS: Record<PluginStatus, string> = {
  enabled: "Enabled",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  stopped: "Stopped",
  completed: "Completed",
  quarantined: "Quarantined",
  failed: "Failed",
  failed_start: "Failed to start",
  failed_stop: "Failed to stop",
  disabled: "Disabled",
  incompatible: "Incompatible",
  unknown: "Unknown",
};

export function pluginStatusLabel(status: string): string {
  return (
    STATUS_LABELS[status as PluginStatus] ??
    status.charAt(0).toUpperCase() + status.slice(1).replaceAll("_", " ")
  );
}

export function pluginStatusTone(
  plugin: Pick<PluginSummary, "status" | "health">,
): StatusTone {
  const status = plugin.status;
  if (
    status === "failed" ||
    status === "failed_start" ||
    status === "failed_stop" ||
    status === "quarantined" ||
    status === "incompatible" ||
    plugin.health === "unhealthy"
  )
    return "danger";
  if (plugin.health === "degraded") return "warn";
  if (status === "running" || status === "enabled" || status === "completed")
    return "good";
  if (status === "starting" || status === "stopping") return "warn";
  return "neutral";
}

export function healthTone(health: PluginSummary["health"]): StatusTone {
  if (health === "healthy") return "good";
  if (health === "degraded") return "warn";
  if (health === "unhealthy") return "danger";
  return "neutral";
}

export function trustLabel(trust: PluginSummary["trust"]): string {
  if (trust?.status === "trusted") {
    if (trust.publisher_channel === "official") return "Official · verified";
    if (trust.publisher_channel === "demo") return "Demo/example · verified";
    return "Community · verified";
  }
  if (trust?.status === "unsigned") return "Unsigned";
  if (trust?.status === "unknown_publisher") return "Unknown publisher";
  if (trust?.status === "invalid_signature") return "Invalid signature";
  return "Unverified";
}

export function trustTone(trust: PluginSummary["trust"]): StatusTone {
  if (trust?.status === "trusted") return "good";
  if (trust?.status === "invalid_signature") return "danger";
  return "warn";
}
