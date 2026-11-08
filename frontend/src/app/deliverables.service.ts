import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

/** A clarifying question. `recommended` is the ONE option the AI recommends (shown first, pre-selected). */
export interface DeliverableQuestion { question: string; options: string[]; recommended?: string; }
/** A typed input a template declares (generic: other companies declare other inputs). */
export interface DeliverableInput {
  key: string; label: string; kind: 'file' | 'files' | 'text' | 'connector' | 'members'; required?: boolean;
  accept?: string; hint?: string; connector?: string;
}
export interface DeliverableTemplate {
  key: string; title: string; description: string; output: string;
  /** 'questions' = answered step by step; 'extract' = content extracted from the declared inputs;
   *  'custom' = a livrable the user designed themselves (see Mes livrables). */
  mode: 'questions' | 'extract' | 'custom';
  custom?: boolean; custom_id?: string; owner?: string; scope?: 'catalog' | 'project'; project_id?: string | null; can_edit?: boolean;
  sections?: { title: string; guidance?: string }[]; instructions?: string;
  inputs: DeliverableInput[];
  questions: DeliverableQuestion[];
  /** Questions that can be pre-filled from the périmètre (project name, client) and skipped. */
  prefill?: { projet?: string; client?: string };
}
export interface SampleSet { set: string; label: string; files: { name: string; size: number }[]; }
export interface MatriceDraft {
  exigences: { ref: string; name: string; impact: string; probabilite: string; source?: string }[];
  cas_usage: { ref: string; name: string; exigences: string[]; source?: string; kind?: string }[];
  notes?: string;
}
export interface PrepareResult {
  /** MatriceDraft for the matrice, the plan draft for the plan de charge (see backend plan.build_plan). */
  status: 'questions' | 'ready'; draft: any; questions: DeliverableQuestion[]; used_llm: boolean; sources: string[];
}

/** A user-designed livrable spec (see backend app/deliverables/custom.py). */
export interface CustomSpec {
  title: string; description?: string; output: 'docx' | 'pptx' | 'xlsx' | 'md'; instructions?: string;
  inputs: DeliverableInput[]; sections: { title: string; guidance?: string }[]; examples?: { name: string; text: string }[];
  /** set → a livrable of that project; null → the company catalogue. */
  project_id?: string | null;
}
export interface CustomDraft {
  title: string; gaps?: string[];
  sections: { heading: string; paragraphs?: string[]; bullets?: string[]; table?: { headers: string[]; rows: string[][] } | null }[];
}

/** Livrables (COPIL, matrice de couverture, …): list templates, prepare from inputs, generate the file. */
@Injectable({ providedIn: 'root' })
export class Deliverables {
  private http = inject(HttpClient);

  /** With a périmètre: what that périmètre can produce (its project's set). Without: the catalogue. */
  templates(perimeterId?: string) {
    return firstValueFrom(this.http.get<{ templates: DeliverableTemplate[] }>('/api/deliverables/templates', { params: perimeterId ? { perimeter_id: perimeterId } : {} }));
  }
  samples(key: string) { return firstValueFrom(this.http.get<{ sets: SampleSet[] }>(`/api/deliverables/${key}/samples`)); }

  /** Inputs (uploaded file ids / connector demo) or a sample set → extraction: questions to answer, or a ready draft. */
  prepare(key: string, body: { inputs?: Record<string, unknown>; samples?: string | null; answers?: Record<string, string>; perimeter_id?: string }) {
    return firstValueFrom(this.http.post<PrepareResult>(`/api/deliverables/${key}/prepare`, body));
  }

  // ---- user-defined livrables ("Mes livrables") ----
  customList() { return firstValueFrom(this.http.get<{ items: DeliverableTemplate[] }>('/api/deliverables/custom')); }
  customGet(id: string) { return firstValueFrom(this.http.get<DeliverableTemplate & { examples_full?: { name: string; text: string }[] }>(`/api/deliverables/custom/${id}`)); }
  customDesign(body: { description: string; example_file_ids?: string[]; examples?: { name: string; text: string }[]; answers?: Record<string, string>; current?: any }) {
    return firstValueFrom(this.http.post<{ status: 'questions' | 'ready'; spec: CustomSpec; questions: DeliverableQuestion[]; used_llm: boolean }>('/api/deliverables/custom/design', body));
  }
  customSave(spec: CustomSpec, id?: string) {
    return firstValueFrom(id ? this.http.put<DeliverableTemplate>(`/api/deliverables/custom/${id}`, spec) : this.http.post<DeliverableTemplate>('/api/deliverables/custom', spec));
  }
  customDelete(id: string) { return firstValueFrom(this.http.delete(`/api/deliverables/custom/${id}`)); }
  customPrepare(id: string, body: { inputs: Record<string, unknown>; answers?: Record<string, string>; perimeter_id?: string; project_id?: string }) {
    return firstValueFrom(this.http.post<{ status: 'questions' | 'ready'; questions: DeliverableQuestion[]; draft: CustomDraft | null; used_llm: boolean }>(`/api/deliverables/custom/${id}/prepare`, body));
  }
  async customGenerate(id: string, body: { draft: CustomDraft; perimeter_id?: string; project_id?: string }): Promise<{ blob: Blob; filename: string }> {
    const res = await firstValueFrom(this.http.post(`/api/deliverables/custom/${id}/generate`, body, { responseType: 'blob', observe: 'response' }));
    const m = /filename="?([^"]+)"?/.exec(res.headers.get('Content-Disposition') || '');
    return { blob: res.body as Blob, filename: m ? m[1] : 'livrable' };
  }

  /** POST answers and/or a confirmed draft; get the generated file back as a Blob + its filename. */
  async generate(key: string, body: { answers?: Record<string, string>; draft?: any; projet?: string; perimeter_id?: string })
    : Promise<{ blob: Blob; filename: string; deliverableId: string | null }> {
    const res = await firstValueFrom(this.http.post(`/api/deliverables/${key}/generate`, body, { responseType: 'blob', observe: 'response' }));
    const cd = res.headers.get('Content-Disposition') || '';
    const m = /filename="?([^"]+)"?/.exec(cd);
    const fallback = key === 'matrice_couverture' ? 'matrice.xlsx' : 'copil.pptx';
    return { blob: res.body as Blob, filename: m ? m[1] : fallback, deliverableId: res.headers.get('X-Deliverable-Id') };
  }
}
