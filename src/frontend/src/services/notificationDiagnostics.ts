// Notification diagnostics: administrator dry runs and test sends, and each
// owner's delivery history. Dry runs never queue work; test sends need an
// explicit confirmation. The server redacts everything it returns.

import { failedRequest } from "./apiError";

const BASE = "/api/settings/notification-diagnostics";

export interface SafeReason {
  code: string;
  label: string;
  action: string;
}
export interface TraceStage extends SafeReason {
  stage: string;
  detail: Record<string, unknown>;
}
export interface DestinationTrace {
  destination_id: string;
  provider_id: string;
  name: string;
  stages: TraceStage[];
}
export interface Simulation {
  dry_run: true;
  fixture: string;
  evaluated_at: number;
  event_type: string;
  purpose: string;
  stages: TraceStage[];
  destinations: DestinationTrace[];
}
export interface Fixture {
  id: string;
  label: string;
  description: string;
  sendable: boolean;
  sensitive: boolean;
}
export interface ProviderHealth extends SafeReason {
  status: string;
  probe: string;
}
export interface ProviderOverview {
  provider_id: string;
  name: string;
  kind: "built_in" | "plugin";
  health: ProviderHealth;
  profile: {
    renderer: string;
    critical_supported: boolean;
    notes: string[];
    capabilities: Record<string, unknown>;
  } | null;
  last_24h: {
    statuses: Record<string, number>;
    error_codes: Record<string, number>;
  };
}
export interface RetryPolicy {
  max_attempts: number;
  backoff_seconds: number[];
  lease_seconds: number;
  transport_timeout_seconds: number;
  delivery_semantics: string;
}
export interface Overview {
  fixtures: Fixture[];
  variants: string[];
  failures: string[];
  providers: ProviderOverview[];
  retry_policy: RetryPolicy;
  max_test_batch: number;
}
export interface RenderedPreview {
  provider_id: string;
  renderer?: string;
  title?: string;
  body?: string;
  html?: string | null;
  actions?: { label: string; url: string }[];
  images?: string[];
  link?: string | null;
  fallbacks?: SafeReason[];
  limits?: Record<string, number>;
  code?: string;
  label?: string;
}
export interface PreviewResult {
  dry_run: true;
  redacted: boolean;
  message?: string;
  variant?: string;
  providers: RenderedPreview[];
}
export interface DigestResult {
  dry_run: true;
  strategy?: string;
  scope?: string;
  items?: number;
  window_start?: number | null;
  window_end?: number | null;
  chunks: { part: number; items: number; title: string; body: string }[];
  reason?: SafeReason;
  code?: string;
  label?: string;
  action?: string;
}
export interface AttemptLog {
  number: number;
  outcome: string;
  operation: string;
  started_at: number;
  finished_at: number | null;
  reason: SafeReason | null;
}
export interface DeliveryRecord {
  id: string;
  event_type: string;
  purpose: string;
  test: boolean;
  provider_id: string;
  destination: string | null;
  status: string;
  operation: string;
  attempts: number;
  max_attempts: number;
  reason: SafeReason | null;
  fallbacks: SafeReason[];
  next_attempt_at: number | null;
  retry_allowed: boolean;
  attempt_log: AttemptLog[];
}
export interface TestSendResult {
  labelled: "test";
  dispatcher: "real";
  queued: number;
  variant: string;
  deliveries: { delivery_id: string; status: string }[];
}

async function request<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? "GET" : "POST",
): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()) as T;
}

export const fetchOverview = () => request<Overview>("/overview");
export const simulate = (body: {
  fixture: string;
  destination_id?: string;
  failure?: string;
  duplicates?: number;
  severity?: string;
  urgency?: string;
}) => request<Simulation>("/simulate", body);
export const previewRender = (body: {
  variant: string;
  provider_id?: string;
  fixture?: string;
}) => request<PreviewResult>("/preview", body);
export const previewDigest = (body: {
  event_type: string;
  provider_id: string;
  count: number;
}) => request<DigestResult>("/digest", body);
export const sendTest = (body: {
  destination_id: string;
  variant: string;
  batch_size: number;
}) => request<TestSendResult>("/test-send", { ...body, confirm: true });
export const fetchDeliveries = (status?: string) =>
  request<{ deliveries: DeliveryRecord[]; retry_policy: RetryPolicy }>(
    `/deliveries${status ? `?status=${encodeURIComponent(status)}` : ""}`,
  );
export const retryDelivery = (id: string) =>
  request<{ id: string; status: string }>(
    `/deliveries/${encodeURIComponent(id)}/retry`,
    {},
  );
