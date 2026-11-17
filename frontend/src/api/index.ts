import { api } from './client'
import type {
  Camera,
  Machine,
  AgentApproval,
  TrainingRunInfo,
  AgentRun,
  RagChatResponse,
  RagChunk,
  RagDocument,
  MaintenanceRequest,
  PdmPrediction,
  CameraInput,
  Evaluation,
  Health,
  Inspection,
  InspectionFilters,
  LoginResponse,
  ModelInput,
  ModelVersion,
  Page,
  Role,
  User,
  ProductSKU,
  ROIZoneSpec,
  InspectionRecipe,
  ProductionBatch,
  OTTriggerResult,
  OTBridgeStatus,
  DefectSpikeEvaluation,
  IncidentTriageResult,
} from '../types'

export const authApi = {
  login: async (username: string, password: string) =>
    (await api.post<LoginResponse>('/auth/login', { username, password })).data,
  me: async () => (await api.get<User>('/auth/me')).data,
  logout: async () => { await api.post('/auth/logout') },
}

export const systemApi = {
  health: async () => (await api.get<Health>('/health')).data,
}

export const cameraApi = {
  list: async () => (await api.get<Camera[]>('/cameras')).data,
  get: async (id: number) => (await api.get<Camera>(`/cameras/${id}`)).data,
  create: async (payload: CameraInput) => (await api.post<Camera>('/cameras', payload)).data,
  update: async (id: number, payload: Partial<CameraInput>) =>
    (await api.put<Camera>(`/cameras/${id}`, payload)).data,
  start: async (id: number) => (await api.post<Camera>(`/cameras/${id}/start`)).data,
  stop: async (id: number) => (await api.post<Camera>(`/cameras/${id}/stop`)).data,
}

export const inspectionApi = {
  list: async (filters: InspectionFilters = {}) => {
    const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value !== undefined))
    return (await api.get<Page<Inspection>>('/inspections', { params })).data
  },
  get: async (id: number) => (await api.get<Inspection>(`/inspections/${id}`)).data,
  inspect: async (file: File, cameraId: number, modelId: number, productId: string, batchId: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('camera_id', String(cameraId))
    form.append('model_version_id', String(modelId))
    if (productId.trim()) form.append('product_id', productId.trim())
    if (batchId.trim()) form.append('batch_id', batchId.trim())
    return (await api.post<Inspection>('/inspections/image', form, { timeout: 120_000 })).data
  },
  evidence: async (id: number) => (await api.get<Blob>(`/inspections/${id}/evidence`, { responseType: 'blob' })).data,
}

export const modelApi = {
  list: async (filters: { active?: boolean; task_type?: string } = {}) =>
    (await api.get<Page<ModelVersion>>('/models/', { params: { page_size: 100, ...filters } })).data,
  create: async (payload: ModelInput) => (await api.post<ModelVersion>('/models/', payload)).data,
  activate: async (id: number) => (await api.post<ModelVersion>(`/models/${id}/activate`)).data,
  deactivate: async (id: number) => (await api.post<ModelVersion>(`/models/${id}/deactivate`)).data,
  evaluations: async (id: number) =>
    (await api.get<{ items: Evaluation[]; total: number }>(`/models/${id}/evaluations`)).data,
}

export const adminApi = {
  createUser: async (payload: { username: string; email?: string; password: string; role: Role }) =>
    (await api.post<User>('/admin/users', payload)).data,
}

export const pdmApi = {
  machines: async () => (await api.get<Machine[]>('/pdm/machines')).data,
  createMachine: async (payload: { name: string; code: string; machine_type?: string; camera_id?: number | null }) =>
    (await api.post<Machine>('/pdm/machines', payload)).data,
  addReading: async (machineId: number, payload: Record<string, number>) =>
    (await api.post(`/pdm/machines/${machineId}/readings`, payload)).data,
  predict: async (machineId: number) =>
    (await api.post<PdmPrediction>(`/pdm/machines/${machineId}/predict`, {}, { timeout: 60_000 })).data,
  correlate: async (machineId: number, autoCreateRequest = true) =>
    (await api.post(`/pdm/machines/${machineId}/correlate`, {}, { params: { auto_create_request: autoCreateRequest } })).data,
  requests: async (status?: string) =>
    (await api.get<MaintenanceRequest[]>('/pdm/requests', { params: status ? { status } : {} })).data,
  createRequest: async (payload: { machine_id: number; title: string; priority: string; description?: string }) =>
    (await api.post<MaintenanceRequest>('/pdm/requests', payload)).data,
  approveRequest: async (id: number, note?: string) =>
    (await api.post<MaintenanceRequest>(`/pdm/requests/${id}/approve`, { note })).data,
  rejectRequest: async (id: number, note?: string) =>
    (await api.post<MaintenanceRequest>(`/pdm/requests/${id}/reject`, { note })).data,
}

export const ragApi = {
  documents: async () => (await api.get<RagDocument[]>('/rag/documents')).data,
  upload: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return (await api.post<RagDocument>('/rag/documents', form, { timeout: 120_000 })).data
  },
  delete: async (id: number) => { await api.delete(`/rag/documents/${id}`) },
  chunks: async (id: number) => (await api.get<RagChunk[]>(`/rag/documents/${id}/chunks`)).data,
  chat: async (question: string) =>
    (await api.post<RagChatResponse>('/rag/chat', { question }, { timeout: 120_000 })).data,
  seedFoodSops: async () =>
    (await api.post<{ status: string; documents: { id: number; title: string; status: string }[] }>('/rag/seed-food-sops')).data,
}

export const agentApi = {
  chat: async (question: string) =>
    (await api.post<AgentRun>('/agent/chat', { question }, { timeout: 120_000 })).data,
  runs: async () => (await api.get<AgentRun[]>('/agent/runs')).data,
  run: async (id: number) => (await api.get<AgentRun>(`/agent/runs/${id}`)).data,
  approvals: async (status?: string) =>
    (await api.get<AgentApproval[]>('/agent/approvals', { params: status ? { status } : {} })).data,
  approve: async (id: number, note?: string) =>
    (await api.post<AgentApproval>(`/agent/approvals/${id}/approve`, { note })).data,
  reject: async (id: number, note?: string) =>
    (await api.post<AgentApproval>(`/agent/approvals/${id}/reject`, { note })).data,
  evaluateDefectSpike: async (payload: {
    defect_category?: string
    window_minutes?: number
    spike_threshold?: number
    camera_id?: number
    auto_propose_approval?: boolean
  } = {}) =>
    (await api.post<DefectSpikeEvaluation>('/agent/evaluate-defect-spike', payload)).data,
  triageIncident: async (payload: {
    incident_type: 'FOREIGN_MATTER' | 'LENS_BLUR_OR_FOG' | 'PSI_DRIFT' | 'MISSING_EXP_DATE' | 'SEAL_DEFECT'
    batch_id?: number
    recipe_id?: number
    camera_id?: number
    zone_id?: string
    affected_qty?: number
    new_threshold?: number
  }) =>
    (await api.post<IncidentTriageResult>('/agent/triage-incident', payload)).data,
}

export const trainingApi = {
  runs: async () => (await api.get<TrainingRunInfo[]>('/training/runs')).data,
  run: async (id: string) => (await api.get<TrainingRunInfo>(`/training/runs/${id}`)).data,
}

export const skuApi = {
  list: async (category?: string) =>
    (await api.get<ProductSKU[]>('/skus', { params: category ? { category } : {} })).data,
  create: async (payload: {
    sku_code: string
    name: string
    category: string
    packaging_type: string
    expiry_format?: string
    is_active?: boolean
  }) => (await api.post<ProductSKU>('/skus', payload)).data,
  update: async (id: number, payload: Partial<ProductSKU>) =>
    (await api.patch<ProductSKU>(`/skus/${id}`, payload)).data,
  delete: async (id: number) => {
    await api.delete(`/skus/${id}`)
  },
}

export const recipeApi = {
  list: async (cameraId?: number) =>
    (await api.get<InspectionRecipe[]>('/recipes', { params: cameraId ? { camera_id: cameraId } : {} })).data,
  create: async (payload: {
    sku_id?: number | null
    sku_code?: string
    name: string
    description?: string
    camera_id?: number | null
    model_version_id?: number | null
    pass_sample_rate?: number
    is_active?: boolean
    preprocessing_config?: Record<string, unknown>
    preprocessing?: Record<string, unknown>
    roi_zones?: Partial<ROIZoneSpec>[]
  }) => (await api.post<InspectionRecipe>('/recipes', payload)).data,
  update: async (
    id: number,
    payload: {
      name?: string
      camera_id?: number | null
      preprocessing_config?: Record<string, unknown>
      roi_zones?: Partial<ROIZoneSpec>[]
    },
  ) => (await api.patch<InspectionRecipe>(`/recipes/${id}`, payload)).data,
  activate: async (id: number) => (await api.post<InspectionRecipe>(`/recipes/${id}/activate`)).data,
  changeover: async (skuCode: string, cameraId?: number) =>
    (await api.post<InspectionRecipe>('/recipes/changeover', { sku_code: skuCode, camera_id: cameraId, source: 'operator_ui' })).data,
}

export const batchApi = {
  list: async (status?: string) =>
    (await api.get<ProductionBatch[]>('/batches', { params: status ? { status } : {} })).data,
  get: async (batchId: number) =>
    (await api.get<ProductionBatch>(`/batches/${batchId}`)).data,
  create: async (payload: {
    batch_code: string
    sku_id?: number | null
    sku_code?: string
    line_name?: string
    shift: string
    target_quantity?: number
    target_qty?: number
    line_id?: number | null
  }) => (await api.post<ProductionBatch>('/batches', payload)).data,
  pause: async (batchId: number) =>
    (await api.post<ProductionBatch>(`/batches/${batchId}/pause`)).data,
  resume: async (batchId: number) =>
    (await api.post<ProductionBatch>(`/batches/${batchId}/resume`)).data,
  close: async (batchId: number) =>
    (await api.post<ProductionBatch>(`/batches/${batchId}/close`)).data,
  sign: async (batchId: number, qaNotes?: string, quarantine = false) =>
    (await api.post<ProductionBatch>(`/batches/${batchId}/sign`, { qa_notes: qaNotes, quarantine })).data,
  exportReport: async (batchId: number, format: 'pdf' | 'csv') =>
    (await api.get<Blob>(`/batches/${batchId}/audit-report`, { params: { format }, responseType: 'blob' })).data,
}

export const otApi = {
  status: async () => (await api.get<OTBridgeStatus>('/ot/status')).data,
  updateConfig: async (
    cameraId: number,
    payload: {
      ot_protocol: string
      ot_host?: string | null
      ot_port?: number | null
      trigger_register?: number | null
      reject_coil?: number | null
      reject_delay_ms: number
      reject_pulse_ms: number
    },
  ) => (await api.patch(`/ot/cameras/${cameraId}/config`, payload)).data,
  testPulse: async (
    cameraId: number,
    payload: {
      actuator_type?: 'pneumatic_air_blast' | 'diverter_arm'
      pulse_ms?: number
      reject_coil?: number
    } = {},
  ) => (await api.post(`/ot/cameras/${cameraId}/test-pulse`, payload)).data,
  trigger: async (
    cameraId: number,
    payload: {
      sensor_id?: string
      protocol?: 'simulated' | 'modbus_tcp' | 'mqtt' | 'gpio_serial'
      actuator_type?: 'pneumatic_air_blast' | 'diverter_arm'
      simulate_defect?: 'seal_leak' | 'missing_exp_date' | 'foreign_matter' | 'burnt' | null
      simulate_blur?: boolean
    } = {},
  ) => (await api.post<OTTriggerResult>(`/ot/trigger/${cameraId}`, payload)).data,
}
