import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

export type Status = 'green' | 'amber' | 'red' | 'grey';

export interface User { username: string; name: string; role: string; clients: string[]; email?: string; permissions?: string[]; can_decide?: boolean; is_admin?: boolean; can_manage_roles?: boolean; must_change_password?: boolean; auth_mode?: string; llm?: string; llm_model?: string | null; }
export interface Installer { filename: string; size: number; platform: 'windows' | 'macos' | 'linux'; label: string; }
export interface DownloadInfo { available: boolean; filename?: string; size?: number; platform?: string; installers?: Installer[]; }
export interface ScoreItem { kpi: string; value: number | null; target: number; unit: string; status: Status; }
export interface Monthly { month: string; incidents_opened: number; incidents_resolved: number; sla_pct: number | null; mttr_p1p2_h: number | null; p1_count: number; }
export interface PortfolioItem {
  id: string; name: string; client: string; client_id: string; description: string; overall_status: Status;
  project?: string | null; project_id?: string | null;
  scorecard: ScoreItem[]; monthly: Monthly[]; applications: number; backlog_open: number; pending_decisions: number; high_risks: number;
}
export interface DeliveryProject {
  id: string; client_id: string; client: string; name: string; description: string; manager: string;
  status: 'active' | 'done' | 'archived'; technologies: string[]; created_at: string;
  perimeter_count?: number; member_count?: number; perimeters?: any[]; members?: ProjectMember[];
}
export interface GovernancePolicy {
  ai_enabled: boolean; default_daily_tokens: number; updated_by: string; updated_at: string | null;
  /** kind: 'agent' = a real agent (orchestrated or conversational); 'feature' = an AI-powered feature. */
  agents: { key: string; label: string; cap: number; used_today: number; kind: 'agent' | 'feature' }[];
}
export interface GovernedUser {
  username: string; name: string; app_blocked: boolean; ai_blocked: boolean; daily_token_quota: number | null;
  effective_quota: number; used_today: number; used_7d: number; note: string; is_self: boolean;
}
export interface ManualPerson { name: string; email?: string; profile?: string; weekly_capacity_h?: number; skills?: (string | { skill: string; level?: number })[]; }
export interface ProjectMember { id: string; project_id: string; owner: string; role_in_project: string; allocation_pct: number; skills?: any[]; name?: string; is_external?: boolean; email?: string; profile?: string | null; weekly_capacity_h?: number | null; }
export interface Source { type: string; ref: string; label: string; excerpt: string; }
export interface Recommendation {
  id: string; perimeter_id: string; run_id: string | null; agent: string; kind: string; title: string; detail: string;
  impact: 'low' | 'medium' | 'high'; confidence: number; sources: Source[]; payload: any; status: string;
  decided_by: string | null; decided_at: string | null; decision_comment: string; created_at: string; perimeter?: string; client?: string;
}
export interface Step { ts: string; node: string; status?: string; message: string; recommendations?: number; llm_used?: boolean; }
export interface Run {
  id: string; perimeter_id: string; workflow: string; status: string; params: any; steps: Step[]; output: any; error: string | null;
  requested_by: string; started_at: string; ended_at: string | null; recommendations?: Recommendation[];
}
export interface Workflow { id: string; label: string; agents: string[]; agent_labels: string[]; }
export interface ChatMessage { role: 'user' | 'assistant'; text: string; ts: string; trace?: { tool: string; input: any; summary: string }[]; sources?: Source[]; actions?: any[]; llm_used?: boolean; }
export interface Conversation { id: string; perimeter_id: string; title: string; messages: ChatMessage[]; updated_at: string; }
export interface AppNotification { id: string; perimeter_id: string | null; ts: string; kind: string; severity: string; title: string; body: string; link: string; read: boolean; perimeter: string; client: string; }
export interface AdminUser { id: string; username: string; email: string; name: string; enabled: boolean; role: string | null; clients: string[]; }
export interface NativeUser { id: string; email: string; name: string; role: string; role_label: string; clients: string[]; active: boolean; must_change_password: boolean; last_login_at: string | null; temporary_password?: string; locked?: boolean; mfa_enabled?: boolean; sign_in_methods?: string[]; }
export interface MicrosoftAdminUser { id: string; email: string; name: string; role: string; role_label: string; clients: string[]; active: boolean; created_at: string | null; last_login_at: string | null; }
export interface RoleInfo { id: string; name: string; label: string; is_system: boolean; locked: boolean; permissions: string[]; user_count: number; }
export interface PermissionInfo { code: string; label: string; category: string; }
export interface LoginResult { access_token: string; expires_in: number; user: any; }
/** Password accepted but the account has two-factor on: exchange mfa_token + a code at /api/auth/mfa. */
export interface MfaChallenge { mfa_required: true; mfa_token: string; }
export interface SignInMethods {
  password: { available: boolean; set: boolean };
  identities: { id: string; provider: string; label: string; email: string; created_at: string | null; last_used_at: string | null }[];
  can_link: { provider: string; label: string }[];
}
export interface DeviceSession { id: number; device: string; ip: string; signed_in_at: string; last_active_at: string; current: boolean; }
export type PolicyAction = 'block' | 'warn' | 'guide';
export interface PolicyRule {
  id: string; title: string; text: string; category: string; action: PolicyAction; keywords: string[];
  active: boolean; proposed: boolean; document_id: string | null; document: string; updated_by: string; updated_at: string | null;
}
export interface PolicyRuleIn { title: string; text: string; category: string; action: PolicyAction; keywords: string[]; active: boolean; }
export interface PolicyDocumentInfo { id: string; filename: string; uploaded_by: string; uploaded_at: string; chars: number; proposed: number; accepted: number; }
export interface PolicyViolation { ts: string; user: string; rule: string; where: string; decision: 'blocked' | 'warned'; detected_by: string; }
export interface PolicyTestResult {
  decision: 'allowed' | 'warned' | 'blocked';
  blocked: { rule: string; action: string; reason: string; by: string }[];
  warned: { rule: string; action: string; reason: string; by: string }[];
}
export interface PublicRule { title: string; text: string; category: string; action: PolicyAction; }
export interface WatchTopic { topic: string; weight: number; reasons: string[]; manual: boolean; muted: boolean; }
export interface WatchItem {
  title: string; url: string; source: string; topic: string; published: string | null;
  relevance: number; category: string; impact: string; affected: string[]; action: string;
}
export interface WatchState {
  briefing: { id: string; created_at: string; topics: string[]; items: WatchItem[]; summary: string; llm_used: boolean; stale: boolean } | null;
  topics: WatchTopic[];
  categories: Record<string, string>;
}
export interface CompanionDataSummary {
  tracking: { allowed: boolean; enabled: boolean; active: boolean; titles: string; retention_days: number; excluded_apps: number };
  proactive_enabled: boolean; memory_learning_allowed: boolean;
  counts: { activity: number; activity_hours: number; messages: number; memories: number; suggestions: number };
  activity_from: string | null; activity_to: string | null;
}
export interface Account {
  username: string; name: string; email: string; role: string; role_label: string; clients: string[]; permissions: string[];
  must_change_password: boolean; auth_mode: string; managed_by: string; can_edit_name: boolean;
  password_url: string | null; account_url: string | null; created_at: string | null; last_login_at: string | null; active_sessions: number | null;
  mfa_enabled: boolean | null; recovery_codes_left: number | null; has_password: boolean | null;
  ai: { enabled: boolean; org_enabled: boolean; blocked: boolean; used_today: number; used_7d: number; daily_quota: number };
}
export interface LlmEntry {
  key: string; label: string; kind: 'default' | 'agent' | 'feature';
  base_url: string; model: string; has_key: boolean; key_hint: string; key_unreadable: boolean;
  updated_by: string; updated_at: string | null;
  effective: { base_url: string; model: string | null; has_key: boolean; source: 'env' | 'default' | 'agent' };
}
export interface LlmSettings {
  mode: string; encryption_weak: boolean;
  env: { base_url: string; has_key: boolean; models: { reasoning: string; agent: string; fast: string } };
  default: LlmEntry; agents: LlmEntry[];
}
export interface LlmConfigIn { base_url?: string; api_key?: string; clear_key?: boolean; model?: string; }
export interface LlmTestResult { ok: boolean; message: string; base_url: string; models?: number; }
export interface AdminUserIn { username: string; email: string; name: string; role: string; clients: string[]; send_invite: boolean; }

/** Current persona in dev auth mode. */
@Injectable({ providedIn: 'root' })
export class Session {
  readonly username = signal<string>(read('dm.user') ?? 'sarah');
  readonly me = signal<User | null>(null);
  /** Bumped when the list of perimeters changes (onboarding). */
  readonly perimetersVersion = signal(0);

  setUser(username: string) {
    this.username.set(username);
    try { localStorage.setItem('dm.user', username); } catch { /* storage unavailable */ }
  }
}

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  me() { return this.http.get<User>('/api/me'); }
  downloadInfo() { return this.http.get<DownloadInfo>('/api/download/info'); }

  // ---- Personal assistant workspace: onboarding scan + cross-source search over the user's own
  // Microsoft 365 data and local files (see backend/app/routes/assistant.py). ----
  assistantStatus() { return this.http.get<{ workspace_id: string; documents: number; chunks: number; by_type: Record<string, number>; ready: boolean }>('/api/assistant/status'); }
  assistantScanMicrosoft(graphToken: string) {
    return this.http.post<{ indexed: Record<string, number>; profile: any; sources_available: number; status: any }>(
      '/api/assistant/scan/microsoft', {}, { headers: { 'X-Graph-Token': graphToken } });
  }
  assistantScanLocal(files: File[]) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    return this.http.post<{ indexed: number; skipped: number; status: any }>('/api/assistant/scan/local', form);
  }
  assistantAsk(question: string, graphToken?: string | null) {
    // Forward the Microsoft token when we have one, so /ask can pull fresh calendar/mail (not just the
    // last scan) for time-sensitive questions like "do I have a meeting today with X?".
    const options = graphToken ? { headers: new HttpHeaders({ 'X-Graph-Token': graphToken }) } : {};
    return this.http.post<{ answer: string; llm_used: boolean; sources: Source[] }>('/api/assistant/ask', { question }, options);
  }
  assistantDocument(body: { title: string; instructions?: string; example?: string; sources_query?: string }) {
    return this.http.post<{ title: string; content: string; gaps: string[]; used_sources: string[]; llm_used: boolean; sources: Source[] }>('/api/assistant/document', body);
  }
  assistantRisk() { return this.http.post<{ headline: string; risks: any[]; llm_used: boolean }>('/api/assistant/risk', {}); }
  devUsers() { return this.http.get<User[]>('/api/dev/users'); }
  workflows() { return this.http.get<{ workflows: Workflow[]; agents: Record<string, string> }>('/api/workflows'); }
  portfolio() { return this.http.get<PortfolioItem[]>('/api/portfolio'); }
  clients() { return this.http.get<any[]>('/api/clients'); }
  perimeter(id: string) { return this.http.get<any>(`/api/perimeters/${id}`); }
  // delivery projects: a manager runs one or more (per client); périmètres sit under a project
  projects() { return this.http.get<DeliveryProject[]>('/api/projects'); }
  project(id: string) { return this.http.get<DeliveryProject>(`/api/projects/${id}`); }
  createProject(body: { client_id: string; name: string; description?: string; manager?: string; technologies?: string[] }) { return this.http.post<DeliveryProject>('/api/projects', body); }
  updateProject(id: string, body: Partial<{ name: string; description: string; manager: string; status: string; technologies: string[] }>) { return this.http.put<DeliveryProject>(`/api/projects/${id}`, body); }
  addProjectMember(id: string, body: { owner: string; role_in_project?: string; allocation_pct?: number }) { return this.http.post<DeliveryProject>(`/api/projects/${id}/members`, body); }
  removeProjectMember(id: string, owner: string) { return this.http.delete<DeliveryProject>(`/api/projects/${id}/members/${encodeURIComponent(owner)}`); }
  attachPerimeter(perimeterId: string, projectId: string | null) { return this.http.put<any>(`/api/perimeters/${perimeterId}/project`, { project_id: projectId }); }
  // plan de charge — a PROJECT-level document (aggregates the project's périmètres' matrices + the project team)
  planPrepare(projectId: string, body: { answers?: Record<string, string>; technologies?: string[] }) { return this.http.post<{ status: 'questions' | 'ready'; draft: any; questions: { question: string; options: string[] }[]; sources: string[] }>(`/api/projects/${projectId}/plan-de-charge/prepare`, body); }
  planGenerate(projectId: string, draft: any) { return this.http.post(`/api/projects/${projectId}/plan-de-charge/generate`, { draft }, { responseType: 'blob', observe: 'response' }); }
  projectDeliverables(projectId: string) { return this.http.get<{ deliverables: any[] }>(`/api/projects/${projectId}/deliverables`); }
  projectDeliverableFile(projectId: string, did: string) { return this.http.get(`/api/projects/${projectId}/deliverables/${did}/file`, { responseType: 'blob', observe: 'response' }); }
  // lifecycle of a périmètre, staffing from the competence matrix, generated deliverables, billing monitoring
  lifecycle(id: string) { return this.http.get<any>(`/api/perimeters/${id}/lifecycle`); }
  staff(id: string) { return this.http.get<{ members: any[] }>(`/api/perimeters/${id}/staff`); }
  addStaff(id: string, body: { owners: string[]; profile?: string; weekly_capacity_h?: number }) { return this.http.post<{ members: any[] }>(`/api/perimeters/${id}/staff`, body); }
  removeStaff(id: string, memberId: string) { return this.http.delete<any>(`/api/perimeters/${id}/staff/${memberId}`); }
  perimeterDeliverables(id: string) { return this.http.get<{ deliverables: any[] }>(`/api/perimeters/${id}/deliverables`); }
  planVsReel(id: string) { return this.http.get<any>(`/api/perimeters/${id}/plan-vs-reel`); }
  mySkills() { return this.http.get<{ owner: string; skills: any[]; categories: string[] }>('/api/team/skills'); }
  setMySkills(skills: any[]) { return this.http.put<{ owner: string; skills: any[] }>('/api/team/skills', { skills }); }
  skillsMatrix() { return this.http.get<{ people: any[] }>('/api/team/skills/matrix'); }
  setUserSkills(owner: string, skills: any[]) { return this.http.put<any>(`/api/team/skills/${encodeURIComponent(owner)}`, { skills }); }
  matchSkills(technologies: string[], perimeterId?: string) { return this.http.post<{ people: any[] }>('/api/team/skills/match', { technologies, perimeter_id: perimeterId ?? null }); }
  kpis(id: string) { return this.http.get<any>(`/api/perimeters/${id}/kpis`); }
  cartography(id: string) { return this.http.get<any>(`/api/perimeters/${id}/cartography`); }
  tickets(id: string, status?: string) { return this.http.get<any[]>(`/api/perimeters/${id}/tickets`, { params: status ? { status } : {} }); }
  backlog(id: string) { return this.http.get<any[]>(`/api/perimeters/${id}/backlog`); }
  search(id: string, query: string) { return this.http.post<any[]>(`/api/perimeters/${id}/search`, { query, k: 6 }); }
  upload(id: string, file: File, docType: string) {
    const form = new FormData();
    form.append('file', file);
    form.append('doc_type', docType);
    return this.http.post<any>(`/api/perimeters/${id}/documents`, form);
  }
  extractContract(id: string) { return this.http.post<Recommendation>(`/api/perimeters/${id}/contract/extract`, {}); }
  updateContract(id: string, contract: any) { return this.http.put<any>(`/api/perimeters/${id}/contract`, contract); }
  billingPreview(id: string, year: number, month: number) { return this.http.get<any>(`/api/perimeters/${id}/billing/preview`, { params: { year, month } }); }
  simulate(id: string, scenario: any) { return this.http.post<any>(`/api/perimeters/${id}/capacity/simulate`, scenario); }

  startRun(id: string, workflow: string, params: any = {}): Observable<Run> { return this.http.post<Run>(`/api/perimeters/${id}/runs`, { workflow, params }); }
  runs(id: string) { return this.http.get<Run[]>(`/api/perimeters/${id}/runs`); }
  run(runId: string) { return this.http.get<Run>(`/api/runs/${runId}`); }

  recommendations(filters: { perimeter_id?: string; status?: string; run_id?: string } = {}) {
    const params: Record<string, string> = {};
    Object.entries(filters).forEach(([k, v]) => { if (v) params[k] = v; });
    return this.http.get<Recommendation[]>('/api/recommendations', { params });
  }
  decide(recId: string, approve: boolean, comment = '') { return this.http.post<Recommendation>(`/api/recommendations/${recId}/decision`, { approve, comment }); }
  audit(perimeterId?: string) { return this.http.get<any[]>('/api/audit', { params: perimeterId ? { perimeter_id: perimeterId } : {} }); }
  /** Searchable audit trail — `total` comes from X-Total-Count (for pagination). */
  auditSearch(f: { q?: string; actor?: string; action?: string; since?: string; until?: string; limit?: number; offset?: number; perimeter_id?: string }) {
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== '') params[k] = String(v);
    return this.http.get<any[]>('/api/audit', { params, observe: 'response' });
  }
  auditFacets() { return this.http.get<{ actions: string[]; actors: string[] }>('/api/audit/facets'); }
  // AI governance (enterprise controls, governance.manage)
  governancePolicy() { return this.http.get<GovernancePolicy>('/api/governance/policy'); }
  saveGovernancePolicy(body: Partial<{ ai_enabled: boolean; default_daily_tokens: number; agent_caps: Record<string, number> }>) { return this.http.put<GovernancePolicy>('/api/governance/policy', body); }
  governanceUsers(q = '') { return this.http.get<{ users: GovernedUser[]; default_daily_tokens: number }>('/api/governance/users', { params: q ? { q } : {} }); }
  saveGovernanceUser(username: string, body: Partial<{ app_blocked: boolean; ai_blocked: boolean; daily_token_quota: number; reset_quota: boolean; note: string }>) {
    return this.http.put<any>(`/api/governance/users/${encodeURIComponent(username)}`, body);
  }
  governanceUsage(days = 7) { return this.http.get<any>('/api/governance/usage', { params: { days } }); }
  // people without an account, entered manually by a manager
  externalPeople() { return this.http.get<{ people: any[] }>('/api/team/external'); }
  // livrables: company catalogue + each project's selection and project-only livrables
  livrableCatalog() { return this.http.get<{ builtins: any[]; custom: any[]; can_manage: boolean }>('/api/deliverables/catalog'); }
  projectLivrables(projectId: string) { return this.http.get<{ catalog: any[]; project_items: any[]; perimeters: { id: string; name: string }[]; can_edit: boolean; can_manage_catalog: boolean }>(`/api/projects/${projectId}/livrables`); }
  setProjectLivrables(projectId: string, keys: string[]) { return this.http.put<{ catalog: any[]; project_items: any[]; perimeters: { id: string; name: string }[]; can_edit: boolean; can_manage_catalog: boolean }>(`/api/projects/${projectId}/livrables`, { keys }); }
  /** Produce a livrable once for the whole project, or per périmètre (perimeter_ids null = all of them). */
  setLivrableScope(projectId: string, key: string, level: 'project' | 'perimeter', perimeter_ids: string[] | null) { return this.http.put<{ catalog: any[]; project_items: any[]; perimeters: { id: string; name: string }[]; can_edit: boolean; can_manage_catalog: boolean }>(`/api/projects/${projectId}/livrables/scope`, { key, level, perimeter_ids }); }
  deleteLivrable(id: string) { return this.http.delete(`/api/deliverables/custom/${id}`); }
  promoteLivrable(id: string) { return this.http.post<any>(`/api/deliverables/custom/${id}/promote`, {}); }
  addManualStaff(perimeterId: string, body: ManualPerson) { return this.http.post<{ members: any[]; owner: string }>(`/api/perimeters/${perimeterId}/staff/manual`, body); }
  addProjectManualMember(projectId: string, manual: ManualPerson, role_in_project = 'member') { return this.http.post<DeliveryProject>(`/api/projects/${projectId}/members`, { manual, role_in_project }); }

  meetings(id: string) { return this.http.get<any[]>(`/api/perimeters/${id}/meetings`); }
  createMeeting(id: string, title: string) { return this.http.post<any>(`/api/perimeters/${id}/meetings`, { title }); }
  meeting(meetingId: string) { return this.http.get<any>(`/api/meetings/${meetingId}`); }
  segment(meetingId: string, speaker: string, text: string) { return this.http.post<any>(`/api/meetings/${meetingId}/segments`, { speaker, text }); }
  askMeeting(meetingId: string, question: string) { return this.http.post<any>(`/api/meetings/${meetingId}/ask`, { question }); }
  /** Local Whisper transcription of a short audio chunk (system/loopback capture — see
   *  call-assistant.ts "Écouter tout l'appel" mode; mic transcription still goes through the
   *  browser's own Web Speech API and never hits this endpoint). */
  transcribeChunk(meetingId: string, chunk: Blob) {
    const form = new FormData();
    form.append('file', chunk, 'chunk.webm');
    return this.http.post<{ text: string }>(`/api/meetings/${meetingId}/transcribe-chunk`, form);
  }
  importTranscript(id: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ meeting: any; run: Run }>(`/api/perimeters/${id}/meetings/import`, form);
  }
  endMeeting(meetingId: string) { return this.http.post<Run>(`/api/meetings/${meetingId}/end`, {}); }

  // copilot
  conversations(id: string) { return this.http.get<Conversation[]>(`/api/perimeters/${id}/conversations`); }
  conversation(convId: string) { return this.http.get<Conversation>(`/api/conversations/${convId}`); }
  chat(id: string, message: string, conversationId?: string | null) { return this.http.post<Conversation>(`/api/perimeters/${id}/copilot`, { message, conversation_id: conversationId ?? null }); }
  deleteConversation(convId: string) { return this.http.delete(`/api/conversations/${convId}`); }

  // notifications & watch
  notifications(unread = false) { return this.http.get<{ unread: number; items: AppNotification[] }>('/api/notifications', { params: unread ? { unread: true } : {} }); }
  markRead(id: string) { return this.http.post(`/api/notifications/${id}/read`, {}); }
  markAllRead() { return this.http.post('/api/notifications/read-all', {}); }
  runWatch() { return this.http.post<{ created: number }>('/api/watch/run', {}); }

  // observability
  observability(days = 30) { return this.http.get<any>('/api/observability', { params: { days } }); }

  // onboarding
  createClient(name: string, sector: string) { return this.http.post<any>('/api/clients', { name, sector }); }
  createPerimeter(client_id: string, name: string, description: string, project_id: string | null = null) { return this.http.post<any>('/api/perimeters', { client_id, name, description, project_id }); }
  datasets() { return this.http.get<any[]>('/api/onboarding/datasets'); }
  importDataset(id: string, dataset: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<any>(`/api/perimeters/${id}/import/${dataset}`, form);
  }
  readiness(id: string) { return this.http.get<any>(`/api/perimeters/${id}/readiness`); }

  // connectors
  connectorKinds() { return this.http.get<Record<string, { label: string; fields: string[] }>>('/api/connectors/kinds'); }
  connectors(id: string) { return this.http.get<any[]>(`/api/perimeters/${id}/connectors`); }
  createConnector(id: string, body: any) { return this.http.post<any>(`/api/perimeters/${id}/connectors`, body); }
  deleteConnector(connId: string) { return this.http.delete(`/api/connectors/${connId}`); }
  testConnector(connId: string) { return this.http.post<{ ok: boolean; message: string }>(`/api/connectors/${connId}/test`, {}); }
  syncConnector(connId: string) { return this.http.post<any>(`/api/connectors/${connId}/sync`, {}); }

  // native auth (email/password). withCredentials carries the httpOnly refresh cookie.
  authLogin(email: string, password: string) { return this.http.post<LoginResult>('/api/auth/login', { email, password }, { withCredentials: true }); }
  authRefresh() { return this.http.post<LoginResult>('/api/auth/refresh', {}, { withCredentials: true }); }
  authLogout() { return this.http.post('/api/auth/logout', {}, { withCredentials: true }); }
  authChangePassword(current_password: string, new_password: string) { return this.http.post<LoginResult>('/api/auth/change-password', { current_password, new_password }, { withCredentials: true }); }
  authLogoutOthers() { return this.http.post<{ revoked: number }>('/api/auth/logout-others', {}, { withCredentials: true }); }
  authMfa(mfa_token: string, code: string) { return this.http.post<LoginResult>('/api/auth/mfa', { mfa_token, code }, { withCredentials: true }); }
  authForgotPassword(email: string) { return this.http.post<{ ok: boolean; message: string }>('/api/auth/forgot-password', { email }); }
  authResetPassword(token: string, new_password: string) { return this.http.post<LoginResult | MfaChallenge>('/api/auth/reset-password', { token, new_password }, { withCredentials: true }); }
  authSessions() { return this.http.get<DeviceSession[]>('/api/auth/sessions', { withCredentials: true }); }
  authRevokeSession(id: number) { return this.http.delete<{ ok: boolean }>(`/api/auth/sessions/${id}`, { withCredentials: true }); }

  // company rules (policy.manage edits; everyone reads the rules in force)
  policyRules() { return this.http.get<{ rules: PolicyRule[]; categories: Record<string, string>; actions: Record<string, string> }>('/api/policy/rules'); }
  createPolicyRule(body: PolicyRuleIn) { return this.http.post<PolicyRule>('/api/policy/rules', body); }
  updatePolicyRule(id: string, body: PolicyRuleIn) { return this.http.put<PolicyRule>(`/api/policy/rules/${id}`, body); }
  acceptPolicyRule(id: string) { return this.http.post<PolicyRule>(`/api/policy/rules/${id}/accept`, {}); }
  deletePolicyRule(id: string) { return this.http.delete(`/api/policy/rules/${id}`); }
  policyDocuments() { return this.http.get<PolicyDocumentInfo[]>('/api/policy/documents'); }
  uploadPolicyDocument(file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ id: string; filename: string; proposed: number; llm_used: boolean }>('/api/policy/documents', form);
  }
  acceptPolicyDocument(id: string) { return this.http.post<{ accepted: number }>(`/api/policy/documents/${id}/accept-all`, {}); }
  deletePolicyDocument(id: string) { return this.http.delete(`/api/policy/documents/${id}`); }
  testPolicy(text: string) { return this.http.post<PolicyTestResult>('/api/policy/test', { text }); }
  policyViolations(days = 30) { return this.http.get<PolicyViolation[]>('/api/policy/violations', { params: { days } }); }
  publicRules() { return this.http.get<PublicRule[]>('/api/policy/public'); }

  // personal tech watch
  techWatch() { return this.http.get<WatchState>('/api/techwatch'); }
  refreshTechWatch() { return this.http.post<WatchState>('/api/techwatch/refresh', {}); }
  setWatchTopics(extra_topics: string[], muted_topics: string[]) { return this.http.put<WatchState>('/api/techwatch/topics', { extra_topics, muted_topics }); }

  // sign-in methods of my account (password + linked Microsoft / Google)
  signInMethods() { return this.http.get<SignInMethods>('/api/account/sign-in-methods'); }
  unlinkSignInMethod(id: string) { return this.http.delete<{ ok: boolean }>(`/api/account/sign-in-methods/${id}`); }

  // two-factor authentication (native accounts)
  mfaSetup(password: string) { return this.http.post<{ secret: string; otpauth_uri: string; qr: string }>('/api/account/mfa/setup', { password }); }
  mfaEnable(code: string) { return this.http.post<{ enabled: boolean; recovery_codes: string[] }>('/api/account/mfa/enable', { code }); }
  mfaDisable(password: string, code: string) { return this.http.post<{ enabled: boolean }>('/api/account/mfa/disable', { password, code }); }
  mfaNewRecoveryCodes(code: string) { return this.http.post<{ recovery_codes: string[] }>('/api/account/mfa/recovery-codes', { code }); }

  // companion: what it holds about me (counts only)
  companionDataSummary() { return this.http.get<CompanionDataSummary>('/api/companion/me/summary'); }

  // interface translation (French source → any language, machine-translated and cached server-side)
  translateUi(lang: string, language_name: string, texts: string[]) {
    return this.http.post<{ lang: string; translations: Record<string, string>; available: boolean; reason: string | null }>(
      '/api/i18n/translate', { lang, language_name, texts });
  }

  // self-service account (every auth mode)
  account() { return this.http.get<Account>('/api/account'); }
  updateAccount(name: string) { return this.http.put<{ name: string }>('/api/account', { name }); }

  // LLM endpoints & API keys per agent (governance.manage)
  llmSettings() { return this.http.get<LlmSettings>('/api/admin/llm'); }
  saveLlmConfig(key: string, body: LlmConfigIn) { return this.http.put<LlmEntry>(`/api/admin/llm/${encodeURIComponent(key)}`, body); }
  resetLlmConfig(key: string) { return this.http.delete<LlmEntry>(`/api/admin/llm/${encodeURIComponent(key)}`); }
  testLlmConfig(key: string, body: LlmConfigIn = {}) { return this.http.post<LlmTestResult>(`/api/admin/llm/${encodeURIComponent(key)}/test`, body); }

  // RBAC admin — roles & permissions
  permissionCatalog() { return this.http.get<PermissionInfo[]>('/api/admin/permissions'); }
  roles() { return this.http.get<RoleInfo[]>('/api/admin/roles'); }
  createRole(name: string, label: string) { return this.http.post<RoleInfo>('/api/admin/roles', { name, label }); }
  setRolePermissions(id: string, permissions: string[]) { return this.http.put<RoleInfo>(`/api/admin/roles/${id}/permissions`, { permissions }); }
  renameRole(id: string, label: string) { return this.http.put<RoleInfo>(`/api/admin/roles/${id}`, { label }); }
  deleteRole(id: string) { return this.http.delete(`/api/admin/roles/${id}`); }

  // RBAC admin — native users
  nativeUsers(search = '') { return this.http.get<NativeUser[]>('/api/admin/users', { params: search ? { search } : {} }); }
  nativeCreateUser(body: { email: string; name: string; role: string; clients: string[]; password?: string }) { return this.http.post<NativeUser>('/api/admin/users', body); }
  nativeUpdateUser(id: string, body: Partial<{ name: string; role: string; clients: string[]; active: boolean }>) { return this.http.put<NativeUser>(`/api/admin/users/${id}`, body); }
  nativeDeleteUser(id: string) { return this.http.delete(`/api/admin/users/${id}`); }
  nativeResetPassword(id: string) { return this.http.post<{ temporary_password: string }>(`/api/admin/users/${id}/reset-password`, {}); }
  nativeUnlock(id: string) { return this.http.post<NativeUser>(`/api/admin/users/${id}/unlock`, {}); }
  nativeResetMfa(id: string) { return this.http.post<NativeUser>(`/api/admin/users/${id}/reset-mfa`, {}); }

  // RBAC admin — Microsoft users (auth_mode=microsoft): they self-provision by signing in, an
  // admin only assigns their role/clients or disables them — no create/password endpoints.
  microsoftUsers(search = '') { return this.http.get<MicrosoftAdminUser[]>('/api/admin/microsoft-users', { params: search ? { search } : {} }); }
  updateMicrosoftUser(id: string, body: Partial<{ role: string; clients: string[]; active: boolean }>) { return this.http.put<MicrosoftAdminUser>(`/api/admin/microsoft-users/${id}`, body); }

  // admin (director only)
  adminStatus() { return this.http.get<{ configured: boolean }>('/api/admin/status'); }
  adminUsers(search = '') { return this.http.get<AdminUser[]>('/api/admin/users', { params: search ? { search } : {} }); }
  adminCreateUser(body: AdminUserIn) { return this.http.post<AdminUser>('/api/admin/users', body); }
  adminUpdateUser(id: string, body: Partial<AdminUserIn> & { enabled?: boolean }) { return this.http.put<AdminUser>(`/api/admin/users/${id}`, body); }
  adminDeleteUser(id: string) { return this.http.delete(`/api/admin/users/${id}`); }
  adminResetPassword(id: string) { return this.http.post(`/api/admin/users/${id}/reset-password`, {}); }

  // Microsoft Graph (via backend relay)
  microsoftMe() { return this.http.get<any>('/api/microsoft/me'); }
  microsoftOverview() { return this.http.get<any>('/api/microsoft/overview'); }

  /** Authenticated file download (exports, templates). */
  download(url: string, fallbackName: string) {
    this.http.get(url, { responseType: 'blob', observe: 'response' }).subscribe(res => {
      const disposition = res.headers.get('Content-Disposition') ?? '';
      const utf8 = /filename\*=UTF-8''([^;]+)/.exec(disposition);
      const plain = /filename="([^"]+)"/.exec(disposition);
      const name = utf8 ? decodeURIComponent(utf8[1]) : plain?.[1] ?? fallbackName;
      const link = document.createElement('a');
      link.href = URL.createObjectURL(res.body!);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    });
  }
}

export const AGENT_LABELS: Record<string, string> = {
  knowledge_transfer: 'Knowledge Transfer', meeting_intelligence: 'Meeting Intelligence', billing: 'Billing',
  capacity: 'Capacity Planning', governance: 'Delivery Governance', incident: 'Incident & Problem', estimation: 'Estimation',
  risk: 'Analyse des risques', doc_generator: 'Générateur de documents',
  copilot: 'Copilot', orchestrator: 'Orchestrateur',
};

export const STATUS_LABEL: Partial<Record<string, string>> = {
  running: 'En cours', awaiting_approval: 'Validation requise', completed: 'Terminé', failed: 'Échec', superseded: 'Remplacé',
  pending: 'À décider', approved: 'Validé', rejected: 'Rejeté', info: 'Information',
};

export function euros(v: number | null | undefined): string {
  return v == null ? '—' : v.toLocaleString('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}
