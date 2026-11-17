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
  event_bus?: { mode: string; connected: boolean; configured: boolean }
  worker?: { mode: string; degraded: boolean; dead_letter_count: number }
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
  ot_protocol?: 'simulated' | 'modbus_tcp' | 'mqtt' | string
  ot_host?: string | null
  ot_port?: number | null
  trigger_register?: number | null
  reject_coil?: number | null
  reject_delay_ms?: number
  reject_pulse_ms?: number
}

export interface CameraInput {
  name: string
  camera_identity?: string | null
  source_type: string
  source_url?: string | null
  resolution?: string | null
  fps_input?: number | null
  is_active?: boolean
  ot_protocol?: string
  ot_host?: string | null
  ot_port?: number | null
  trigger_register?: number | null
  reject_coil?: number | null
  reject_delay_ms?: number
  reject_pulse_ms?: number
}

export interface ModelVersion {
  stage?: 'development' | 'staging' | 'production'
  previous_stage?: string | null
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
  food_features?: {
    sealer_temp_c?: number | null
    pneumatic_pressure_bar?: number | null
    zone_temp_c?: number | null
    conveyor_speed_mpm?: number | null
    motor_current_a?: number | null
    motor_current_amp?: number | null
    vibration_mm_s?: number | null
    ambient_humidity_pct?: number | null
  } | null
  vision_correlation?: {
    correlated: boolean
    camera_id: number | null
    recent_fail_count: number
    seal_leak_count: number
    seal_defect_count?: number
    sealer_temp_c?: number | null
    pneumatic_pressure_bar?: number | null
    maintenance_request_id?: number | null
    diagnosis: string | null
  } | null
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

// ── AI Operations Agent (Phase D) ───────────────────────────────────────────

export interface AgentToolLog {
  tool: string
  status: string
  duration_ms: number
  error: string | null
  args?: Record<string, unknown> | null
}

export interface AgentRun {
  id: number
  question: string
  status: string
  provider: string
  response: string | null
  error: string | null
  duration_ms: number | null
  created_at: string
  tool_logs?: AgentToolLog[]
  plan?: { tool: string; args: Record<string, unknown> }[] | null
}

export interface AgentApproval {
  id: number
  tool_name: string
  args: Record<string, unknown> | null
  status: string
  requested_by_user_id: number
  decided_by_user_id: number | null
  decision_note: string | null
  expires_at: string
  result: Record<string, unknown> | null
  created_at: string
}

// ── ML Laboratory (Phase G) ─────────────────────────────────────────────────

export interface CheckpointInfo {
  path: string
  exists: boolean
  sha256: string | null
  size_bytes: number | null
}

export interface TrainingRunInfo {
  run_id: string
  task: string
  seed: number
  status: string
  config: Record<string, unknown> | null
  dataset: { name?: string; fingerprint?: string; kind?: string } | null
  metrics: Record<string, unknown> | null
  reason: string | null
  created_at: string | null
  completed_at: string | null
  run_dir: string
  checkpoints: CheckpointInfo[]
}

// ── Food Division Enhancements (Priorities 1-3) ─────────────────────────────

export interface ProductSKU {
  id: number
  org_id: number
  sku_code: string
  name: string
  category: string
  packaging_type: string
  expiry_format?: string | null
  is_active: boolean
  created_at: string
}

export interface ROIZoneSpec {
  zone_id: string
  zone_name: string
  name?: string
  x: number
  y: number
  width: number
  height: number
  bbox: number[]
  task_type: 'object_detection' | 'anomaly_segmentation' | 'ocr_expiry'
  zone_type?: 'seal_integrity' | 'expiry_code' | 'product_quality'
  model_id?: number | null
  confidence_threshold: number
  defect_category: 'SEAL_DEFECT' | 'MISSING_EXP_DATE' | 'BURNT_OR_BROKEN' | 'FOREIGN_MATTER' | string
  enabled: boolean
  anomaly_threshold?: number
  ocr_required?: boolean
  ocr_regex?: string
  min_ocr_confidence?: number
  max_burnt_ratio?: number
  burnt_v_max?: number
  detection_confidence?: number
  reject_labels?: string[]
}

export interface InspectionRecipe {
  id: number
  org_id: number
  sku_id: number | null
  sku_code: string | null
  name: string
  description: string | null
  line_id: number | null
  camera_id: number | null
  model_version_id: number | null
  is_active: boolean
  pass_sample_rate: number
  preprocessing_config: {
    enabled?: boolean
    clahe_enabled: boolean
    clahe_clip_limit: number
    clahe_tile_size?: number
    white_balance_enabled?: boolean
    gamma: number
    blur_detection_enabled?: boolean
    blur_threshold: number
    illumination_guard_enabled?: boolean
    min_luminance?: number
    max_luminance?: number
    hsv_mask_enabled?: boolean
    hsv_lower?: number[]
    hsv_upper?: number[]
  }
  preprocessing_json: {
    enabled: boolean
    clahe_enabled: boolean
    clahe_clip_limit: number
    clahe_tile_size: number
    gamma: number
    blur_detection_enabled: boolean
    blur_threshold: number
  }
  roi_zones: ROIZoneSpec[]
  zones_json: ROIZoneSpec[]
  created_at: string
}

export interface ProductionBatch {
  id: number
  org_id: number
  organization_name?: string
  batch_code: string
  sku_id?: number | null
  sku_code: string
  sku_name?: string | null
  recipe_id: number | null
  line_id: number | null
  line_name?: string | null
  shift: string
  started_by?: number | null
  operator_id: number | null
  operator_username?: string | null
  status: 'ACTIVE' | 'PAUSED' | 'CLOSED' | 'active' | 'completed' | 'quarantined' | string
  target_quantity: number
  target_qty: number
  total_inspected: number
  total_pass: number
  total_fail: number
  total_review: number
  first_pass_yield_pct: number
  yield_pct: number
  reject_rate_pct: number
  defect_counts: Record<string, number>
  qa_signed_by_user_id: number | null
  qa_signed_by_username: string | null
  qa_signature_hash: string | null
  qa_signed_at: string | null
  qa_notes: string | null
  started_at: string | null
  closed_at?: string | null
  ended_at: string | null
}

export interface OTTriggerResult {
  trigger_event_id: number
  inspection_id: number
  camera_id: number
  camera_status: string
  batch_code: string | null
  sku_code: string | null
  decision: Decision
  reason: string
  defect_categories: string[]
  zones: Record<string, unknown>[]
  preprocessing: {
    laplacian_variance: number
    sharpness_score?: number
    blur_threshold: number
    mean_luminance?: number
    std_luminance?: number
    lighting_ok?: boolean
    quality_status?: string
    is_blurred: boolean
    status: string
    clahe_applied: boolean
    white_balance_applied?: boolean
    gamma: number
    hsv_mask_enabled?: boolean
    hsv_in_range_ratio?: number | null
  }
  rejector: {
    actuated: boolean
    protocol: string
    actuator_type: string
    plc_target: string
    pulse_ms: number
    transport_status: string
  }
  evidence_saved: boolean
  inference_latency_ms: number
  total_cycle_ms: number
  trigger_to_reject_ms?: number
  within_30ms_target: boolean
}

export interface OTBridgeStatus {
  default_protocol: string
  supported_protocols: string[]
  supported_actuators: string[]
  plc_endpoint: string
  reject_pulse_ms: number
  target_inference_ms: number
  total_triggers: number
  total_rejector_actuations: number
  avg_inference_latency_ms: number | null
  avg_total_cycle_ms: number | null
  avg_trigger_to_reject_ms: number | null
  max_trigger_to_reject_ms: number | null
}

export interface DefectSpikeEvaluation {
  status: string
  spike_detected: boolean
  defect_category: string
  window_minutes: number
  matched_fail_count: number
  total_fail_in_window: number
  spike_threshold: number
  sop_citations: { citation: string; chunk_id: number; document_id: number; snippet: string }[]
  pdm_correlation: Record<string, unknown> | null
  approval_id: number | null
  recommended_action: string
}

export interface IncidentTriageResult {
  status: string
  incident_type: 'FOREIGN_MATTER' | 'LENS_BLUR_OR_FOG' | 'PSI_DRIFT' | 'MISSING_EXP_DATE' | 'SEAL_DEFECT' | string
  sop_answer: string
  sop_provider: string
  sop_citations: RagCitation[]
  line_quality_summary: Record<string, unknown>
  mitigation_action: Record<string, unknown> | null
  approval_id: number | null
}
