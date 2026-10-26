import { api } from './client'
import type {
  Camera,
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
