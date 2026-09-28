import type {
  AnalysisResult,
  BriefComplaint,
  Category,
  Complaint,
  ComplaintDraft,
  ComplaintHistory,
  Department,
  DocumentChunk,
  EscalationRule,
  GenAIConfig,
  KnowledgeDocument,
  Metrics,
  PriorityRule,
  Rule,
  SlaPolicy,
  StaffMember,
  Trends,
  User,
} from './types'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
export const UNAUTHORIZED_EVENT = 'supportnova:unauthorized'

class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

// FastAPI returns `detail` as a string, a list of strings (our validators), or a list of
// {loc, msg} objects (request-model validation). Show all three readably.
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          const loc = Array.isArray((item as { loc?: unknown[] }).loc) ? (item as { loc: unknown[] }).loc.filter((p) => p !== 'body').join('.') : ''
          return `${loc ? `${loc}: ` : ''}${String((item as { msg: unknown }).msg)}`
        }
        return JSON.stringify(item)
      })
      .join(' ')
  }
  return fallback
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('supportnova_token')
  const isForm = options.body instanceof FormData
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError(0, `Cannot reach the SupportNova API at ${API_URL}. Is the backend running?`)
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const payload = await response.json()
      detail = describe(payload.detail, detail)
    } catch {
      // Keep the status-based message when the response has no JSON body.
    }
    if (response.status === 401 && token) {
      localStorage.removeItem('supportnova_token')
      localStorage.removeItem('supportnova_role')
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(response.status, detail)
  }

  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) return response as unknown as T
  return response.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })

export function queryString(params: Record<string, string | number | boolean | undefined | null>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export type ReportKey =
  | 'complaints'
  | 'comparison'
  | 'escalations'
  | 'sla'
  | 'manual_review'
  | 'departments'
  | 'policy_usage'
  | 'resolution_compliance'

export const api = {
  async login(email: string, password: string) {
    const payload = await request<{ access_token: string; role: string }>('/api/v1/auth/login-json', json('POST', { email, password }))
    localStorage.setItem('supportnova_token', payload.access_token)
    return payload
  },
  health: () => request<{ status: string; database: string; genai_configured: boolean }>('/health'),
  me: () => request<User>('/api/v1/auth/me'),

  complaints: (query = '') => request<Complaint[]>(`/api/v1/complaints${query}`),
  complaint: (id: number) => request<Complaint>(`/api/v1/complaints/${id}`),
  history: (id: number) => request<ComplaintHistory>(`/api/v1/complaints/${id}/history`),
  submitComplaint: (draft: ComplaintDraft) =>
    request<{ complaint: Complaint; near_duplicate: boolean; near_duplicate_of?: string }>('/api/v1/complaints', json('POST', draft)),
  uploadAttachment: (id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ id: number; filename: string }>(`/api/v1/complaints/${id}/attachments`, { method: 'POST', body: form })
  },
  analyze: (id: number, tone = 'professional', skipGenAI = false) =>
    request<AnalysisResult>(`/api/v1/complaints/${id}/analyze`, json('POST', { tone, skip_genai: skipGenAI })),
  updateStatus: (id: number, status: string, note = '') => request<Complaint>(`/api/v1/complaints/${id}/status`, json('PATCH', { status, note })),
  assign: (id: number, body: { agent_id?: number; department_id?: number }) => request<Complaint>(`/api/v1/complaints/${id}/assign`, json('POST', body)),
  review: (id: number, action: string, comments: string, finalDecision: Record<string, unknown> = {}) =>
    request<Complaint>(`/api/v1/complaints/${id}/review`, json('POST', { action, comments, final_decision: finalDecision })),
  manualReview: () => request<Complaint[]>('/api/v1/complaints/queue/manual-review'),
  customerDecision: (id: number, action: 'confirm' | 'reopen', comment = '') =>
    request<Complaint>(`/api/v1/complaints/${id}/customer-decision`, json('POST', { action, comment })),

  adminMetrics: () => request<Metrics>('/api/v1/dashboards/admin'),
  analytics: () => request<Metrics>('/api/v1/analytics'),
  trends: (days = 7) => request<Trends>(`/api/v1/analytics/trends?days=${days}`),
  agentDashboard: () =>
    request<{ assigned: BriefComplaint[]; queue: BriefComplaint[]; escalation_warnings: BriefComplaint[]; sla_risks: BriefComplaint[] }>('/api/v1/dashboards/agent'),
  customerDashboard: () =>
    request<Array<{ id: number; complaint_code: string; title: string; status: string; submitted_date: string; department: string | null; latest_update: string; resolution_status: string }>>(
      '/api/v1/dashboards/customer',
    ),

  departments: () => request<Department[]>('/api/v1/config/departments'),
  categories: () => request<Category[]>('/api/v1/config/categories'),
  rules: () => request<Rule[]>('/api/v1/config/rules'),
  escalationRules: () => request<EscalationRule[]>('/api/v1/config/escalation-rules'),
  slaPolicies: () => request<SlaPolicy[]>('/api/v1/config/sla-policies'),
  priorityRules: () => request<PriorityRule[]>('/api/v1/config/priority-rules'),
  genaiConfig: () => request<GenAIConfig>('/api/v1/config/genai'),
  resetGenai: () => request<{ reset: string[] }>('/api/v1/config/genai/reset', { method: 'POST' }),
  createDepartment: (body: { code: string; name: string; description?: string }) => request<Department>('/api/v1/config/departments', json('POST', body)),
  createCategory: (body: { code: string; name: string; default_department_code: string; subcategories: Array<{ code: string; name: string; keywords: string[] }> }) =>
    request<{ id: number }>('/api/v1/config/categories', json('POST', body)),
  createRule: (body: Record<string, unknown>) => request<{ rule_code: string; warnings: string[] }>('/api/v1/config/rules', json('POST', body)),
  toggleRule: (code: string, isActive: boolean) => request<{ is_active: boolean }>(`/api/v1/config/rules/${encodeURIComponent(code)}/active`, json('PATCH', { is_active: isActive })),
  createEscalationRule: (body: Record<string, unknown>) => request<EscalationRule>('/api/v1/config/escalation-rules', json('POST', body)),
  updateEscalationRule: (code: string, body: Record<string, unknown>) => request<EscalationRule>(`/api/v1/config/escalation-rules/${encodeURIComponent(code)}`, json('PATCH', body)),
  updateSla: (code: string, body: { first_response_minutes: number; resolution_hours: number }) => request<SlaPolicy>(`/api/v1/config/sla-policies/${encodeURIComponent(code)}`, json('PUT', body)),
  updatePriorityRule: (urgency: string, priority: string) => request<PriorityRule>(`/api/v1/config/priority-rules/${urgency}`, json('PUT', { priority })),

  users: () => request<User[]>('/api/v1/users'),
  staff: () => request<StaffMember[]>('/api/v1/users/staff'),
  createUser: (body: { email: string; full_name: string; password: string; role: string; customer_type?: string }) => request<User>('/api/v1/users', json('POST', body)),
  setUserActive: (id: number, active: boolean) => request<User>(`/api/v1/users/${id}/active?is_active=${active}`, { method: 'PATCH' }),

  documents: () => request<KnowledgeDocument[]>('/api/v1/knowledge-base/documents'),
  documentChunks: (id: number) => request<DocumentChunk[]>(`/api/v1/knowledge-base/documents/${id}/chunks`),
  setDocumentStatus: (id: number, status: string) =>
    request<{ status: string; superseded_versions: string[]; affected_complaint_codes: string[] }>(`/api/v1/knowledge-base/documents/${id}/status?status=${status}`, { method: 'PATCH' }),
  uploadDocument: (form: FormData) =>
    request<{ id: number; document_code: string; chunks: number; affected_open_complaints: number; affected_complaint_codes: string[]; superseded_versions: string[]; warnings: string[] }>(
      '/api/v1/knowledge-base/documents',
      { method: 'POST', body: form },
    ),

  downloadReport: async (format: 'csv' | 'xlsx' | 'pdf', report: ReportKey = 'complaints') => {
    const token = localStorage.getItem('supportnova_token')
    const response = await fetch(`${API_URL}/api/v1/reports/export?fmt=${format}&report=${report}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) throw new ApiError(response.status, 'Could not export report')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `supportnova-${report}.${format}`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
}

export { ApiError, API_URL }
