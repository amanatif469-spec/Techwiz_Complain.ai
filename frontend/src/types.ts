export type Role = 'customer' | 'agent' | 'reviewer' | 'manager' | 'administrator'

export interface User {
  id: number
  email: string
  full_name: string
  role: Role
  is_active: boolean
}

export interface SecondaryIssue {
  issue_category?: string
  subcategory?: string
  department?: string
  rule_code?: string | null
}

export interface ComplaintIntelligence {
  complaint_id?: string
  primary_issue?: string
  secondary_issues?: Array<string | SecondaryIssue>
  issue_category?: string
  subcategory?: string
  sentiment?: string
  emotion_indicators?: string[]
  urgency?: string
  priority?: string
  department?: string
  supporting_departments?: string[]
  entities?: Record<string, unknown>
  policy_id?: string
  policy_section?: string
  policy_version?: string | null
  policy_applicability?: string
  resolution_steps?: string[]
  required_actions?: string[]
  prohibited_actions?: string[]
  escalation_required?: boolean
  escalation_level?: string
  escalation_reason?: string
  escalation_reasons?: string[]
  escalation_notes?: string
  response_type?: string
  customer_response?: string
  follow_up_required?: boolean
  follow_up_communication?: string
  clarification_questions?: string[]
  complaint_summary?: string
  agent_guidance?: string[]
  missing_information?: string[]
  refund_eligible?: boolean | null
  replacement_eligible?: boolean | null
  compensation_permitted?: boolean
  compensation_recommended?: boolean
  rule_code?: string | null
  related_complaints?: string[]
  prompt_injection?: { detected: boolean; patterns?: string[] }
}

export interface ValidationFlag {
  code?: string
  detail?: string
  action?: string
  value?: string
  field?: string
  patterns?: string[]
}

export interface Complaint {
  id: number
  complaint_code: string
  title: string
  description: string
  status: string
  product_or_service: string
  order_reference: string
  previous_complaint_reference?: string
  customer_type: string
  customer_code?: string | null
  channel?: string
  preferred_contact_channel?: string
  requested_resolution?: string
  latest_update: string
  sla_risk: boolean
  sla_resolution_due?: string
  follow_up_at?: string
  is_repeat?: boolean
  is_adversarial?: boolean
  duplicate_of?: string | null
  department?: string | null
  assigned_department_id?: number | null
  assigned_to_id?: number | null
  assigned_to?: string | null
  created_at: string
  updated_at?: string
  analyzed_at?: string | null
  attachments?: Array<{ id: number; filename: string; size_bytes: number }>
  genai?: ComplaintIntelligence | null
  python?: ComplaintIntelligence | null
  checks?: { review_reasons?: string[]; genai_skipped_reason?: string; policy?: Record<string, unknown> } & Record<string, unknown> | null
  flags?: ValidationFlag[]
  verification_score?: number | null
  requires_manual_review?: boolean | null
  pending_review?: boolean
  comparison?: {
    fields?: Record<string, { genai: unknown; python: unknown; match: boolean }>
    status?: string
    explanation?: string
  } | null
  genai_meta?: {
    provider: string
    model: string
    prompt_version: string
    attempt: number
    latency_ms?: number
    analyzed_at?: string
    policy_versions?: Array<{ document_code: string; version: string; status?: string }>
    error: string
    available?: boolean
    stale?: boolean
  } | null
  open_followups?: Array<{ type: string; message: string; scheduled_at: string }>
  latest_review?: { action: string; comments: string; final_decision: Record<string, unknown>; created_at: string } | null
}

export interface Metrics {
  total: number
  analyzed?: number
  statuses: Record<string, number>
  categories: Record<string, number>
  departments: Record<string, number>
  priorities: Record<string, number>
  urgencies?: Record<string, number>
  sentiments: Record<string, number>
  products?: Record<string, number>
  escalations: number
  sla_risks: number
  genai_python_mismatches: number
  genai_compared?: number
  verified_matches?: number
  agreement_rate?: number | null
  average_verification_score?: number | null
  sla_compliance?: number | null
  average_resolution_hours?: number | null
  manual_review_cases: number
  pending_reviews?: number
  repeat_complaints: number
}

export interface Trends {
  window_days: number
  daily: Record<string, Record<string, number>>
  rising_categories: Array<{ category: string; current: number; previous: number; note: string }>
  recurring_product_issues: Array<{ product: string; category: string; count: number; note: string }>
  escalation_spikes: Array<{ date: string; escalations: number; note: string }>
  repeated_service_failures: number
}

export interface BriefComplaint {
  id: number
  complaint_code: string
  title: string
  status: string
  created_at: string
  category?: string
  priority?: string
  sentiment?: string
  genai_recommendation?: string
  validation_status?: string
  verification_score?: number | null
  escalation?: boolean
  escalation_level?: string
  sla_risk?: boolean
  suggested_response?: string
}

export interface Department {
  id: number
  code: string
  name: string
  description?: string
  is_active?: boolean
}

export interface Category {
  id: number
  code: string
  name: string
  is_active: boolean
  default_department?: string | null
  subcategories: Array<{ code: string; name: string; keywords: string[] }>
}

export interface Rule {
  id: number
  rule_code: string
  category_code: string
  subcategory_code: string
  department_code: string
  urgency: string
  priority: string
  escalation_required: boolean
  escalation_level: string
  policy_code: string
  policy_section: string
  conditions: { keywords?: string[] }
  required_actions: string[]
  prohibited_actions: string[]
  is_active: boolean
}

export interface EscalationRule {
  rule_code: string
  name: string
  keywords: string[]
  categories: string[]
  customer_types: string[]
  min_repeat_count: number
  escalation_level: string
  force_urgency: string | null
  reason: string
  is_active: boolean
}

export interface SlaPolicy {
  code: string
  name: string
  customer_type: string | null
  priority: string
  first_response_minutes: number
  resolution_hours: number
}

export interface PriorityRule {
  urgency: string
  priority: string
}

export interface GenAIConfig {
  primary: string
  chain: Array<{ provider: string; model: string }>
  configured: boolean
  paused: Record<string, number>
  max_retries: number
  timeout_seconds: number
  total_budget_seconds: number
  prompt: { name: string; active_version: string; versions: string[] }
  thresholds: { high_value_threshold: number; repeat_similarity_threshold: number; default_department_code: string }
}

export interface KnowledgeDocument {
  id: number
  document_code: string
  title: string
  version: string
  category: string
  status: string
  usable?: boolean
  effective_date?: string
  expiry_date?: string
  chunk_count: number
}

export interface DocumentChunk {
  chunk_code: string
  section: string
  heading: string
  page_number: number | null
  version: string
  content: string
}

export interface ComplaintHistory {
  audit: Array<{ at: string; actor: string; action: string; details: Record<string, unknown> }>
  reviews: Array<{ at: string; reviewer: string; action: string; comments: string; original_recommendation: Record<string, unknown>; final_decision: Record<string, unknown> }>
  genai_runs: Array<{ at: string; provider: string; model: string; prompt_version: string; attempt: number; latency_ms: number; valid: boolean; error: string }>
  followups: Array<{ scheduled_at: string; type: string; message: string; completed: boolean }>
}

export interface StaffMember {
  id: number
  full_name: string
  role: Role
}

export interface ComplaintDraft {
  title: string
  description: string
  product_or_service: string
  order_reference: string
  previous_complaint_reference: string
  customer_type: string
  customer_code?: string
  preferred_contact_channel: string
  requested_resolution: string
}

export interface AnalysisResult {
  complaint: Complaint
  requires_manual_review: boolean
  review_reasons?: string[]
  genai_error?: string
  genai_skipped_reason?: string
}
