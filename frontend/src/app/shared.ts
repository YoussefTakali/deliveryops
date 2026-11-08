import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AGENT_LABELS, Api, Recommendation, STATUS_LABEL, Session } from './api';

const KIND_LABEL: Partial<Record<string, string>> = {
  risk: 'Risque', knowledge: 'Connaissance', onboarding_plan: "Plan d'onboarding", problem: 'Problème', duplicate: 'Doublon', scope: 'Hors périmètre',
  capacity_alert: 'Alerte capacité', staffing: 'Staffing', billing: 'Facturation', margin: 'Marge', prebilling: 'Pré-facture', drift: 'Dérive',
  sla: 'SLA', trend: 'Tendance', copil: 'COPIL', action: 'Action', minutes: 'Compte-rendu', estimate: 'Chiffrage', contract_rules: 'Règles contrat',
};

@Component({
  selector: 'app-rec',
  imports: [DatePipe, DecimalPipe, RouterLink],
  template: `
    @let r = rec();
    <article class="rec" [class.decided]="r.status !== 'pending'">
      <div class="top">
        <span class="pill" [class.red]="r.impact === 'high'" [class.amber]="r.impact === 'medium'">{{ impactLabel[r.impact] }}</span>
        <span class="pill accent">{{ kindLabel[r.kind] ?? r.kind }}</span>
        <span class="faint small">{{ agentLabel[r.agent] }}</span>
        @if (showPerimeter() && r.perimeter) { <span class="faint small">· {{ r.client }} / {{ r.perimeter }}</span> }
        <span class="spacer"></span>
        <span class="small faint" title="Confiance de l'agent">confiance {{ r.confidence * 100 | number: '1.0-0' }} %</span>
      </div>
      <h3>{{ r.title }}</h3>
      @if (r.detail) { <p class="detail pre" [class.clamp]="!open()">{{ r.detail }}</p> }

      @if (open()) {
        @if (r.kind === 'copil' && r.payload?.headline) {
          <div class="payload">
            <strong>Points forts</strong><ul>@for (x of r.payload.highlights; track $index) { <li>{{ x }}</li> }</ul>
            <strong>Points d'attention</strong><ul>@for (x of r.payload.concerns; track $index) { <li>{{ x }}</li> }</ul>
            <strong>Décisions demandées au client</strong><ul>@for (x of r.payload.decisions_requested; track $index) { <li>{{ x }}</li> }</ul>
            <strong>Plan d'action</strong>
            <table><tr><th>Action</th><th>Porteur</th><th>Échéance</th><th>KPI</th></tr>
              @for (a of r.payload.action_plan; track $index) { <tr><td>{{ a.action }}</td><td>{{ a.owner }}</td><td>{{ a.due }}</td><td>{{ a.kpi_impacted }}</td></tr> }
            </table>
          </div>
        }
        @if (r.kind === 'onboarding_plan' && r.payload?.weeks) {
          <div class="payload weeks">
            @for (w of r.payload.weeks; track w.week) {
              <div><strong>Semaine {{ w.week }} — {{ w.focus }}</strong>
                <ul>@for (a of w.activities; track $index) { <li>{{ a }}</li> }</ul>
                <span class="small muted">Livrables : {{ w.deliverables.join(', ') }}</span></div>
            }
          </div>
          @if (r.payload.app_cards?.length) {
            <strong class="small">Fiches applicatives</strong>
            <div class="payload weeks">
              @for (c of r.payload.app_cards; track c.application) {
                <div class="appcard"><strong>{{ c.application }}</strong>
                  <div class="small muted">{{ c.purpose }}</div>
                  @if (c.operations.length) { <div class="small"><em>Exploitation</em><ul>@for (x of c.operations; track $index) { <li>{{ x }}</li> }</ul></div> }
                  @if (c.known_issues.length) { <div class="small"><em>Incidents connus</em><ul>@for (x of c.known_issues; track $index) { <li>{{ x }}</li> }</ul></div> }
                  @if (c.watch_points.length) { <div class="small"><em>Vigilance</em><ul>@for (x of c.watch_points; track $index) { <li>{{ x }}</li> }</ul></div> }
                </div>
              }
            </div>
            <span class="small faint">Les fiches sont versées dans la base de connaissance à la validation du plan.</span>
          }
        }
        @if (r.kind === 'estimate' && r.payload?.assumptions) {
          <div class="payload"><strong>Hypothèses</strong><ul>@for (x of r.payload.assumptions; track $index) { <li>{{ x }}</li> }</ul>
            <strong>Risques</strong><ul>@for (x of r.payload.risks; track $index) { <li>{{ x }}</li> }</ul></div>
        }
        @if (r.sources.length) {
          <div class="sources">
            <span class="small muted">Sources</span>
            @for (s of r.sources; track $index) {
              <div class="src"><span class="pill">{{ s.type }}</span> <span>{{ s.label }}</span>
                @if (s.excerpt) { <div class="small faint excerpt">« {{ s.excerpt }} »</div> }</div>
            }
          </div>
        }
      }

      <div class="foot">
        <button class="ghost sm" (click)="open.set(!open())">{{ open() ? 'Réduire' : 'Détails' + (r.sources.length ? ' et sources (' + r.sources.length + ')' : '') }}</button>
        @if (r.run_id && showRun()) { <a class="small" [routerLink]="['/runs', r.run_id]">Voir l'exécution</a> }
        <span class="spacer"></span>
        @if (r.status === 'pending' || r.status === 'info') {
          @if (canDecide()) {
            @if (rejecting()) {
              <input class="comment" placeholder="Motif du rejet (obligatoire)" [value]="comment()" (input)="comment.set($any($event.target).value)" />
              <button class="sm ko" [disabled]="!comment().trim() || busy()" (click)="decide(false)">Confirmer le rejet</button>
              <button class="sm ghost" (click)="rejecting.set(false)">Annuler</button>
            } @else {
              <button class="sm ko" (click)="rejecting.set(true)">Rejeter</button>
              <button class="sm ok" [disabled]="busy()" (click)="decide(true)">Valider</button>
            }
          } @else if (r.status === 'pending') {
            <span class="small faint">Validation réservée aux Delivery Managers</span>
          }
        } @else {
          <span class="pill" [class.green]="r.status === 'approved'" [class.red]="r.status === 'rejected'">{{ statusLabel[r.status] ?? r.status }}</span>
          @if (r.decided_by) { <span class="small faint">par {{ r.decided_by }} le {{ r.decided_at | date: 'dd/MM HH:mm' }}</span> }
          @if (r.decision_comment) { <span class="small muted">« {{ r.decision_comment }} »</span> }
        }
      </div>
      @if (error()) { <p class="small" style="color: var(--red)">{{ error() }}</p> }
    </article>
  `,
  styles: `
    .rec { border: 1px solid var(--border); border-radius: 9px; padding: 12px 14px; background: var(--surface); display: flex; flex-direction: column; gap: 6px; }
    .rec.decided { background: var(--surface-2); }
    .top, .foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .spacer { flex: 1; }
    .detail { color: var(--muted); font-size: 13px; }
    .clamp { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .payload { font-size: 13px; background: var(--surface-2); border-radius: 8px; padding: 10px 12px; }
    .payload ul { margin: 4px 0 10px; padding-left: 18px; }
    .weeks { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .appcard { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; background: var(--surface); }
    .appcard ul { margin: 2px 0 6px; }
    .sources { display: flex; flex-direction: column; gap: 6px; border-top: 1px dashed var(--border); padding-top: 8px; }
    .src { font-size: 13px; }
    .excerpt { margin: 2px 0 0 4px; }
    .comment { width: 260px; padding: 4px 8px; font-size: 12px; }
  `,
})
export class RecCard {
  private api = inject(Api);
  private session = inject(Session);
  rec = input.required<Recommendation>();
  showPerimeter = input(false);
  showRun = input(true);
  decided = output<Recommendation>();

  protected open = signal(false);
  protected rejecting = signal(false);
  protected comment = signal('');
  protected busy = signal(false);
  protected error = signal('');
  protected canDecide = computed(() => !!this.session.me()?.can_decide);
  protected impactLabel = { high: 'Impact fort', medium: 'Impact moyen', low: 'Impact faible' };
  protected kindLabel = KIND_LABEL;
  protected agentLabel = AGENT_LABELS;
  protected statusLabel = STATUS_LABEL;

  decide(approve: boolean) {
    this.busy.set(true);
    this.error.set('');
    this.api.decide(this.rec().id, approve, this.comment()).subscribe({
      next: r => { this.busy.set(false); this.rejecting.set(false); this.decided.emit(r); },
      error: e => { this.busy.set(false); this.error.set(e.error?.detail ?? 'Erreur'); },
    });
  }
}

/** Minimal SVG chart: bars for one series, optional line for a second (own scale), optional target line. */
@Component({
  selector: 'app-chart',
  template: `
    <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" class="chart" role="img" [attr.aria-label]="ariaLabel()">
      @for (t of ticks(); track t.v) {
        <line [attr.x1]="padL" [attr.x2]="W - padR" [attr.y1]="t.y" [attr.y2]="t.y" class="grid" />
        <text [attr.x]="padL - 6" [attr.y]="t.y + 4" text-anchor="end" class="axis">{{ t.v }}</text>
      }
      @if (targetY() !== null) {
        <line [attr.x1]="padL" [attr.x2]="W - padR" [attr.y1]="targetY()" [attr.y2]="targetY()" class="target" />
        <text [attr.x]="W - padR" [attr.y]="targetY()! - 5" text-anchor="end" class="axis">{{ targetLabel() }}</text>
      }
      @for (b of bars(); track $index) {
        <rect [attr.x]="b.x" [attr.y]="b.y" [attr.width]="b.w" [attr.height]="b.h" rx="3" [class]="'bar ' + (b.cls || '')"><title>{{ b.label }} : {{ b.v }}</title></rect>
        <text [attr.x]="b.x + b.w / 2" [attr.y]="H - 6" text-anchor="middle" class="axis">{{ b.label }}</text>
      }
      @if (linePath()) {
        <path [attr.d]="linePath()" class="line" />
        @for (p of linePoints(); track $index) { <circle [attr.cx]="p.x" [attr.cy]="p.y" r="3" class="pt"><title>{{ p.v }}</title></circle> }
      }
    </svg>
  `,
  styles: `
    .chart { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--border); stroke-width: 1; }
    .axis { fill: var(--faint); font-size: 10px; }
    .bar { fill: var(--accent); opacity: .85; }
    .bar.over { fill: var(--red); } .bar.tension { fill: var(--amber); } .bar.soft { fill: var(--faint); opacity: .45; }
    .line { fill: none; stroke: var(--text); stroke-width: 2; }
    .pt { fill: var(--surface); stroke: var(--text); stroke-width: 2; }
    .target { stroke: var(--red); stroke-dasharray: 4 3; }
  `,
})
export class Chart {
  labels = input<string[]>([]);
  values = input<(number | null)[]>([]);
  classes = input<string[]>([]);
  line = input<(number | null)[] | null>(null);
  target = input<number | null>(null);
  targetLabel = input('');
  ariaLabel = input('Graphique');
  max = input<number | null>(null);

  readonly W = 560; readonly H = 190; readonly padL = 34; readonly padR = 8; readonly padT = 12; readonly padB = 22;

  private top = computed(() => {
    const vals = [...this.values(), this.target() ?? 0].filter((v): v is number => v != null);
    const m = this.max() ?? Math.max(1, ...vals);
    const step = Math.pow(10, Math.floor(Math.log10(m)));
    return Math.ceil(m / step) * step;
  });
  private y(v: number) { return this.padT + (1 - v / this.top()) * (this.H - this.padT - this.padB); }

  ticks = computed(() => [0, .5, 1].map(f => ({ v: Math.round(this.top() * f), y: this.y(this.top() * f) })));
  targetY = computed(() => (this.target() == null ? null : this.y(this.target()!)));
  bars = computed(() => {
    const n = this.values().length || 1;
    const slot = (this.W - this.padL - this.padR) / n;
    const w = Math.min(38, slot * 0.62);
    return this.values().map((v, i) => {
      const val = v ?? 0;
      return { x: this.padL + i * slot + (slot - w) / 2, y: this.y(val), w, h: Math.max(0, this.H - this.padB - this.y(val)), v, label: this.labels()[i] ?? '', cls: this.classes()[i] };
    });
  });
  linePoints = computed(() => {
    const line = this.line();
    if (!line) return [];
    const vals = line.filter((v): v is number => v != null);
    const lo = Math.min(...vals) - 5, hi = Math.max(...vals) + 2;
    const n = line.length || 1;
    const slot = (this.W - this.padL - this.padR) / n;
    return line.map((v, i) => (v == null ? null : { x: this.padL + i * slot + slot / 2, y: this.padT + (1 - (v - lo) / (hi - lo || 1)) * (this.H - this.padT - this.padB - 10), v }))
      .filter((p): p is { x: number; y: number; v: number } => p !== null);
  });
  linePath = computed(() => this.linePoints().map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' '));
}


/** Minimal Markdown for AI answers: escapes HTML first, then quotes, bullets, **bold** and _italic_.
 *  Use with [innerHTML] (Angular sanitizes it again). */
export function renderMd(text: string | null | undefined): string {
  const esc = (text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return esc
    .split('\n')
    .map(line => {
      if (/^&gt;\s?/.test(line)) return `<blockquote>${line.replace(/^&gt;\s?/, '')}</blockquote>`;
      if (/^\s*[-•*]\s+/.test(line)) return `<div>• ${line.replace(/^\s*[-•*]\s+/, '')}</div>`;
      return line ? `<div>${line}</div>` : '<div style="height:6px"></div>';
    })
    .join('')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])_(.+?)_(?=\s|$|<)/g, '$1<em>$2</em>');
}
