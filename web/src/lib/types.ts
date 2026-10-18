export type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

export type CatKey = "fg_pct" | "ft_pct" | "fg3m" | "pts" | "reb" | "ast" | "stl" | "blk" | "tov";
export type CatMap = Partial<Record<CatKey, number>>;

export interface Draft {
  draft_id: string;
  league_key: string | null;
  status: string;
  num_teams: number;
  num_rounds: number;
  my_slot: number;
  order_type: string;
  config: { team_names?: Record<string, string> } & Record<string, unknown> | null;
  is_mock: boolean | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DraftPick {
  draft_id: string;
  overall_pick: number;
  round: number;
  team_slot: number;
  player_id: string | null;
  player_name: string | null;
  source: string | null;
  created_at: string | null;
}

export interface RecItem {
  player_id: string;
  name?: string;
  team?: string;
  positions?: string[];
  score?: number;
  value?: number;
  marginal_value?: number;
  prob_available_next?: number;
  adp?: number | null;
  cat_impact?: CatMap;
  reason?: string;
}

export interface RecPayload {
  draft_id?: string;
  as_of_pick?: number;
  for_pick?: number;
  my_next_picks?: number[];
  model_run_id?: string;
  generated_at?: string;
  recommendations?: RecItem[];
  team_profile?: {
    categories?: Partial<Record<CatKey, { z?: number; win_prob?: number }>>;
    punts?: string[];
    expected_category_wins?: number;
  };
  roster?: { player_id: string; name?: string; positions?: string[]; overall_pick?: number }[];
  strategy?: { name?: string; notes?: string };
  warnings?: string[];
}

export interface DraftRecommendation {
  rec_id: string;
  draft_id: string;
  as_of_pick: number;
  for_pick: number | null;
  model_run_id: string | null;
  payload: RecPayload;
  created_at: string | null;
}

export interface ModelRun {
  run_id: string;
  kind: string;
  model_version: string;
  git_sha: string | null;
  params: Json | null;
  inputs: Json | null;
  summary: Json | null;
  created_at: string | null;
}

export interface PlayerValue {
  run_id: string;
  player_id: string;
  rank: number | null;
  value: number | null;
  cat_values: CatMap | null;
  adp: number | null;
  positions: string[] | null;
  extra: Record<string, unknown> | null;
}

export interface Player {
  player_id: string;
  name: string;
  team: string | null;
  positions: string[] | null;
  status: string | null;
  injury_note: string | null;
  age: number | null;
}

export interface Projection {
  run_id: string;
  player_id: string;
  season: string;
  gp: number | null;
  gp_sd: number | null;
  mpg: number | null;
  fgm: number | null;
  fga: number | null;
  ftm: number | null;
  fta: number | null;
  fg3m: number | null;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  stl: number | null;
  blk: number | null;
  tov: number | null;
  extra: Record<string, unknown> | null;
}

export interface AgentRun {
  run_id: string;
  agent: string;
  task: string | null;
  status: string;
  mode: string | null;
  git_sha: string | null;
  session_ref: string | null;
  summary: string | null;
  started_at: string | null;
  heartbeat_at: string | null;
  ended_at: string | null;
}

export interface AgentEvent {
  event_id: string;
  run_id: string;
  ts: string | null;
  kind: string;
  message: string | null;
  payload: Json | null;
}

export interface Decision {
  decision_id: string;
  run_id: string | null;
  kind: string;
  model_recommendation: Json | null;
  choice: Json | null;
  agrees_with_model: boolean | null;
  rationale: string | null;
  created_at: string | null;
}

export interface Approval {
  approval_id: string;
  run_id: string | null;
  action_type: string;
  summary: string | null;
  payload: Json | null;
  status: string;
  decided_by: string | null;
  note: string | null;
  requested_at: string | null;
  decided_at: string | null;
  expires_at: string | null;
}

export interface Transaction {
  txn_id: string;
  idempotency_key: string;
  run_id: string | null;
  kind: string;
  payload: Json | null;
  status: string;
  approval_id: string | null;
  verification: Json | null;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Lock {
  resource: string;
  holder_run_id: string;
  purpose: string | null;
  acquired_at: string | null;
  expires_at: string | null;
}

export interface Task {
  task_id: string;
  title: string;
  description: string | null;
  area: string | null;
  priority: number | null;
  status: string;
  owner: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DataSnapshot {
  snapshot_id: string;
  source: string;
  uri: string | null;
  path: string | null;
  sha256: string | null;
  rows: number | null;
  meta: Json | null;
  fetched_at: string | null;
}

/** league_snapshots: read-only observations of Yahoo league pages posted by Meta Muse. */
export interface LeagueSnapshot {
  snapshot_id: string;
  ts: string | null;
  reported_by: string;
  kind: string;
  week: number | null;
  observed_at: string | null;
  source_url: string | null;
  payload: Json;
  payload_sha256: string;
  summary: string | null;
  last_seen_at: string | null;
}
