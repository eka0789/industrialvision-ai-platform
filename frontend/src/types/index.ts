export type Role = 'admin' | 'operator' | 'ai_engineer'
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
