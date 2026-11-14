export type Role = 'owner' | 'admin' | 'operator' | 'ai_engineer' | 'viewer'
export type Decision = 'PASS' | 'FAIL' | 'REVIEW' | 'ERROR'

export interface User {
  id: number
  username: string
  role: Role
  is_active: boolean
  email?: string | null
}

export interface LoginResponse {
  token: {
    access_token: string
    token_type: string
    role: Role
    user_id: number
  }
  user: User
  refresh_token: string
}

export interface Health {
  status: string
  environment: string
  version: string
  database: string
  camera_connections: number
}

export interface Camera {
  id: number
  name: string
  camera_identity: string | null
  source_type: string
  source_url?: string | null
  status: string
  line_id: number | null
  resolution: string | null
  fps_input: number | null
  last_inspection_at: string | null
  last_error?: string | null
  is_active?: boolean
}

export interface CameraInput {
  name: string
  camera_identity?: string | null
  source_type: string
  source_url?: string | null
  resolution?: string | null
  fps_input?: number | null
  is_active?: boolean
}

export interface ModelVersion {
  id: number
  name: string
  description: string | null
  framework: string
  framework_version: string | null
  task_type: string | null
  artifact_id: string | null
  version: string | null
  license: string | null
  checksum: string | null
  classes: string[] | null
  thresholds: Record<string, number> | null
  provenance: Record<string, unknown> | null
  source_path: string | null
  device: string
  config_json: Record<string, unknown> | null
  is_active: boolean
  status: string
}

export interface ModelInput {
  name: string
  description?: string
  framework: string
  framework_version?: string
  task_type: string
  source_path: string
  version?: string
  license: string
  classes: string[]
  device: string
  config_json?: Record<string, unknown>
  is_active: boolean
}

export interface Detection {
  label: string
  confidence: number
  bbox: { x1: number; y1: number; x2: number; y2: number } | null
  area: number | null
}

export interface Inspection {
  id: number
  timestamp: string
  camera_id: number
  session_id: number
  product_id: string | null
  batch_id: string | null
  model_name: string | null
  model_version: string | null
  detections: Detection[]
  anomaly: { score: number | null; heatmap_path: string | null; threshold: number | null } | null
  decision: Decision
  decision_reason: string | null
  latency_ms: number | null
  evidence_path: string | null
  error_status: string | null
  error_message: string | null
  metadata: Record<string, unknown> | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface InspectionFilters {
  page?: number
  page_size?: number
  decision?: Decision | ''
  camera_id?: number
  batch_id?: string
}

export interface Evaluation {
  id: number
  model_version_id: number
  task_type: string
  metrics_json: Record<string, unknown> | null
  hardware: string | null
  input_size: string | null
  batch_size: number | null
  measured_at: string | null
}

// ── Predictive Maintenance (Phase B) ────────────────────────────────────────

export type MachineStatus = 'running' | 'idle' | 'fault' | 'maintenance'

export interface Machine {
  id: number
  org_id: number
  name: string
  code: string
  machine_type: string
  camera_id: number | null
  status: MachineStatus
  created_at: string
}

export interface PdmPrediction {
  machine_id: number
  mode: 'model' | 'rules-only-fallback' | 'error'
  model_selected?: string | null
  note?: string | null
  error?: string | null
  failure_probability: number | null
  rul_minutes: number | null
  health: number | null
  failure_modes: string[]
  recommendation: string
  reading_id?: number | null
}

export interface MaintenanceRequest {
  id: number
  machine_id: number
  title: string
  description: string | null
  priority: 'low' | 'medium' | 'high' | 'critical'
  status: 'pending' | 'approved' | 'rejected' | 'completed' | 'cancelled'
  requested_by_user_id: number
  decided_by_user_id: number | null
  decision_note: string | null
  created_at: string
}

// ── Knowledge AI / RAG (Phase C) ────────────────────────────────────────────

export interface RagDocument {
  id: number
  title: string
  filename?: string
  status: 'pending' | 'processing' | 'ready' | 'failed'
  error?: string | null
  chunk_count: number
  embedding_provider?: string | null
  embedding_dim?: number | null
  size_bytes?: number
  created_at?: string
}

export interface RagCitation {
  index: number
  chunk_id: number
  document_id: number
  page: number | null
  snippet: string
}

export interface RagChatResponse {
  status: 'answered' | 'refused'
  answer: string
  provider: string
  citations: RagCitation[]
}

export interface RagChunk {
  id: number
  ordinal: number
  page: number | null
  content: string
  sha256: string
}
