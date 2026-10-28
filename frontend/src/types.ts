export interface MatchRow {
  id: string
  match_id: string | null
  radiant_name: string | null
  dire_name: string | null
  duration_s: number
  patch: string | null
  parse_status: string
}

export interface Player {
  slot: number
  team: number
  hero: string | null
  player_name: string | null
  role: number | null
  role_source: string | null
}

export interface InventorySnapshot {
  slot: number
  requested_time_s: number
  snapshot_time_s: number | null
  tick?: number
  items: Record<string, string>
  coverage: string
}

export interface Position {
  time_s: number
  x: number
  y: number
}

export interface Ward {
  team: number | null
  kind: string | null
  time_s: number
  end_s: number | null
  end_reason: string | null
  x: number | null
  y: number | null
}

export interface MatchEvent {
  time_s: number
  type: string
  subtype: string | null
  team: number | null
  slot: number | null
  description: string | null
}

export interface MapFeature {
  feature_id: string
  kind: string
  team: number | null
  name: string | null
  x: number
  y: number
}

export interface Observation {
  type: string
  time_s: number | null
  end_s: number | null
  team: number | null
  evidence: string
  details: Record<string, unknown>
}

export interface MatchDetail {
  match: MatchRow
  players: Player[]
  positions: Record<string, Position[]>
  wards: Ward[]
  events: MatchEvent[]
  map_features: MapFeature[]
  map_observations: Observation[]
  map_feature_coverage: string | null
  tower_identity_coverage: string | null
  map_objective_coverage: string | null
  creep_coverage: string | null
  ability_event_coverage: Record<string, string> | null
}

export interface AgentCitation {
  match_id: string
  time_s: number | null
  label: string
}

export interface AgentAnalysis {
  kind: string
  [key: string]: any
}

export interface AgentTurn {
  role: 'user' | 'assistant'
  text: string
  analysis?: AgentAnalysis
  citations?: AgentCitation[]
}

export interface AgentStatus {
  ready: boolean
  mode: string
  model_configured: boolean
  model?: string
  message: string
}
