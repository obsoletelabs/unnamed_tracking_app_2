// Host-owned latest-state story pages: the signed-in owner's private view,
// shareable live links, and plugin actions that the server reauthorises.

import { failedRequest } from "./apiError";
import type { RichNode } from "../utils/richNodes";

export interface StoryFact {
  key: string;
  label: string;
  kind: string;
  text: string;
  iso?: string;
  ts?: number;
}
export interface StoryImage {
  index: number;
  role: string;
  alt: string;
  state: "proxied" | "external" | "host_media" | "fallback";
  url: string | null;
}
export interface StoryAction {
  id: string;
  label: string;
  style: "default" | "primary" | "danger";
  kind: "link" | "route" | "action";
  href?: string;
}
export interface StoryView {
  title: string;
  summary: RichNode[];
  preview: string;
  blocks: RichNode[][];
  facts: StoryFact[];
  images: StoryImage[];
  actions: StoryAction[];
  attribution: { label: string; url: string | null } | null;
  expires_at: number | null;
  stale: boolean;
}
export interface StoryPage {
  storyId: string;
  revision: number;
  phase: string;
  status: string;
  updatedAt: number;
  source: string;
  severity: "info" | "warning" | "error";
  rich: boolean;
  title: string;
  text: string | null;
  view: StoryView | null;
}
export interface LiveLink {
  id: string;
  createdAt: number;
  expiresAt: number;
  revokedAt: number | null;
  lastUsedAt: number | null;
  url?: string;
}

interface BackendStory {
  story_id: string;
  revision: number;
  phase: string;
  status: string;
  updated_at: number;
  source: string;
  severity: "info" | "warning" | "error";
  rich: boolean;
  title: string;
  text: string | null;
  view: StoryView | null;
}
interface BackendLink {
  id: string;
  created_at: number;
  expires_at: number;
  revoked_at: number | null;
  last_used_at: number | null;
  url?: string;
}

const STORIES = "/api/notifications/stories";
const JSON_HEADERS = { "Content-Type": "application/json" };

function story(raw: BackendStory): StoryPage {
  return {
    storyId: raw.story_id,
    revision: raw.revision,
    phase: raw.phase,
    status: raw.status,
    updatedAt: raw.updated_at,
    source: raw.source,
    severity: raw.severity,
    rich: raw.rich,
    title: raw.title,
    text: raw.text,
    view: raw.view,
  };
}
function link(raw: BackendLink): LiveLink {
  return {
    id: raw.id,
    createdAt: raw.created_at,
    expiresAt: raw.expires_at,
    revokedAt: raw.revoked_at,
    lastUsedAt: raw.last_used_at,
    url: raw.url,
  };
}
async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await failedRequest(response);
  return (await response.json()) as T;
}

export async function fetchStory(storyId: string): Promise<StoryPage> {
  const response = await fetch(`${STORIES}/${encodeURIComponent(storyId)}`, {
    credentials: "include",
  });
  return story(await json<BackendStory>(response));
}

export async function fetchStoryByLink(token: string): Promise<StoryPage> {
  const response = await fetch(
    `${STORIES}/by-link/${encodeURIComponent(token)}`,
    { credentials: "include" },
  );
  return story(await json<BackendStory>(response));
}

export async function listLiveLinks(storyId: string): Promise<LiveLink[]> {
  const response = await fetch(
    `${STORIES}/${encodeURIComponent(storyId)}/live-links`,
    { credentials: "include" },
  );
  return (await json<{ items: BackendLink[] }>(response)).items.map(link);
}

export async function createLiveLink(
  storyId: string,
  days: number,
): Promise<LiveLink> {
  const response = await fetch(
    `${STORIES}/${encodeURIComponent(storyId)}/live-links`,
    {
      method: "POST",
      credentials: "include",
      headers: JSON_HEADERS,
      body: JSON.stringify({ days }),
    },
  );
  return link(await json<BackendLink>(response));
}

export async function revokeLiveLink(
  storyId: string,
  linkId: string,
): Promise<void> {
  const response = await fetch(
    `${STORIES}/${encodeURIComponent(storyId)}/live-links/${encodeURIComponent(linkId)}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) throw await failedRequest(response);
}

export async function runStoryAction(
  storyId: string,
  actionId: string,
  confirm: boolean,
): Promise<{ ok: boolean; message: string | null }> {
  const response = await fetch(
    `${STORIES}/${encodeURIComponent(storyId)}/actions/${encodeURIComponent(actionId)}`,
    {
      method: "POST",
      credentials: "include",
      headers: JSON_HEADERS,
      body: JSON.stringify({ confirm }),
    },
  );
  return await json<{ ok: boolean; message: string | null }>(response);
}
