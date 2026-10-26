import type { GameLink, GameRelationshipType } from "../types/game";
import type { PageOverrides } from "../utils/gamePage";

// FastAPI's full game response. Keep this separate from the UI model and
// compact collection index so a partial record cannot overwrite detail fields.
export interface BackendGame {
  play_compatibility?: {
    min_players: number | null;
    max_players: number | null;
    duration_minutes: number | null;
    crossplay: boolean;
  } | null;
  provider_ids?: Record<string, string>;
  playnite_guid?: string | null;
  locked_fields?: string[];
  id: string;
  title: string;
  sort_title: string;
  has_key_art?: boolean | null;
  description: string | null;
  release_date: string | null;
  developer: string | null;
  publisher: string | null;
  series: string | null;
  tags: string[];
  features: string[];
  source: string | null;
  platform: string | null;
  region: string | null;
  language: string | null;
  age_rating: string | null;
  parent_game_id: string | null;
  relationship_type: GameRelationshipType | null;
  time_to_beat_hours: number | string | null;
  time_to_beat_extras_hours?: number | string | null;
  time_to_beat_completionist_hours?: number | string | null;
  time_to_beat_all_hours?: number | string | null;
  status: string;
  priority: string | null;
  favorite: boolean;
  notes: string | null;
  resume_note: string | null;
  playtime_seconds: number;
  purchase_date: number | null;
  completion_date: number | null;
  purchase_price: number | string | null;
  purchase_price_currency_code: string | null;
  physical_condition: string | null;
  rating_story: number | string | null;
  rating_gameplay: number | string | null;
  rating_soundtrack: number | string | null;
  rating_overall: number | string | null;
  personal_rank: number | null;
  // Unix timestamps in seconds, not ISO strings.
  created_at: number;
  updated_at: number;
  folder_location: string;
  last_played_at: number | null;
  stale_since: number | null;
  profiles_enabled: boolean;
  page_settings?: PageOverrides | null;
  osrs_stats_enabled: boolean;
  collections: string[];
  links: GameLink[];
}
