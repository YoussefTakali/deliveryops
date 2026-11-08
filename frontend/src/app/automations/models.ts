// Shapes of the "Automatisations" API (shared contract, sections 1–4).

export type AutomationStatus = 'draft' | 'active' | 'paused';
export type RunStatus = 'awaiting_confirmation' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'skipped';
export type RunTrigger = 'schedule' | 'mail' | 'manual' | 'test';
export type Executor = 'server' | 'local' | 'control';

export interface ScheduleTrigger {
  type: 'schedule';
  every: 'day' | 'week' | 'month' | 'hours';
  at?: string;
  weekdays?: number[];
  day_of_month?: number;
  interval_hours?: number;
  timezone?: string;
}
export interface MailTrigger { type: 'mail'; from_contains?: string; subject_contains?: string; has_attachment?: boolean; }
export type Trigger = { type: 'manual' } | ScheduleTrigger | MailTrigger;

export interface Step {
  id: string;
  type: string;
  title?: string;
  params: Record<string, any>;
  foreach?: string;
  when?: string;
  on_error?: 'stop' | 'continue';
}

// type 'dataset': value is an array of row objects; the whole automation replays once per row,
// exposing the current row as {{row.<column>}}. See AutomationRunner.datasetRows.
export interface AutomationInput { label: string; type: 'file' | 'text' | 'dataset'; value?: any; }

export interface Automation {
  id: string;
  owner?: string;
  name: string;
  description: string;
  status: AutomationStatus;
  trigger: Trigger;
  steps: Step[];
  inputs: Record<string, AutomationInput>;
  require_confirmation: boolean;
  approved_script_hashes: string[];
  source?: { kind: 'recording' | 'prompt'; recording_id?: string; prompt?: string } | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: RunStatus | null;
  mail_trigger_checked_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface RunLogEntry { ts: string; step_id?: string | null; level: 'info' | 'warn' | 'error'; message: string; output_summary?: string | null; }

export interface AutomationRun {
  id: string;
  automation_id: string;
  trigger: RunTrigger;
  dry_run: boolean;
  status: RunStatus;
  context?: any;
  log: RunLogEntry[];
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface CatalogParam {
  name: string;
  type: string; // string | text | number | boolean | file_id | file_ids | emails | object | choice
  required: boolean;
  description: string;
  options?: string[];
  default?: any;
}
export interface CatalogEntry { type: string; title: string; executor: Executor; side_effect: boolean; params: CatalogParam[]; output: string; }

export interface ValidationIssue { step_id?: string | null; message: string; }
export interface ValidationResult { errors: ValidationIssue[]; warnings: (ValidationIssue | string)[]; }

export interface CompileResult {
  name: string;
  description: string;
  trigger: Trigger;
  steps: Step[];
  inputs: Record<string, { label: string; type: 'file' | 'text' | 'dataset'; value?: any }>;
  questions: string[];
  warnings: (string | ValidationIssue)[];
  explanation: string;
}

export interface FileMeta { id?: string; file_id?: string; name: string; size?: number; content_type?: string; }

/** Minimal "file reference" used in step outputs/params: {file_id, name}. */
export interface FileRef { file_id: string; name?: string; size?: number; content_type?: string; }

export interface RecordingKeyframe { t: number; window_title: string; process: string; reason?: string; jpeg: Uint8Array; }
export interface RecordingBundle {
  duration_s: number;
  screen?: { width: number; height: number; scale_factor: number };
  hook: boolean;
  limit_reason: string | null;
  events: any[];
  narration: { t: number; text: string }[];
  keyframes: RecordingKeyframe[];
}
export interface RecordingAnalysis { summary: string; observed_steps: { t: number; app: string; action: string; detail: string }[]; }
