import { Component, computed, input, model, output } from '@angular/core';

export interface Choice { question: string; options: string[]; recommended?: string; }

/** Pre-select the AI's recommended option for every question that has no answer yet. */
export function seedRecommended(questions: Choice[], answers: Record<string, string>): Record<string, string> {
  const out = { ...answers };
  for (const q of questions) if (q.recommended && !(out[q.question] ?? '').trim()) out[q.question] = q.recommended;
  return out;
}

/** One clarifying question as clickable option cards (the AI's ONE recommended option
 *  carries a « Recommandé » badge and is pre-selected by the caller), plus an always-visible "Autre"
 *  field to type your own answer. Two-way bound: [(value)]. */
@Component({
  selector: 'app-choices',
  template: `
    <div class="qblock">
      @if (showTitle()) { <div class="qtitle">{{ q().question }}</div> }
      @if (q().options.length) {
        <div class="qopts" role="radiogroup" [attr.aria-label]="q().question">
          @for (o of q().options; track o) {
            <button type="button" role="radio" class="opt" [class.on]="!typing() && value() === o" [attr.aria-checked]="!typing() && value() === o" (click)="value.set(o)">
              <span class="radio"></span><span class="olabel">{{ o }}</span>
              @if (o === q().recommended) { <span class="rec" title="L'option recommandée par l'IA">Recommandé</span> }
            </button>
          }
        </div>
      }
      <input class="other" [placeholder]="q().options.length ? 'Autre — écrivez votre réponse' : 'Votre réponse'"
             [value]="typing() ? value() : ''" (input)="value.set($any($event.target).value)" (keydown.enter)="enter.emit()" />
    </div>
  `,
  styles: `
    .qblock { display: flex; flex-direction: column; gap: 8px; }
    .qtitle { font-weight: 600; font-size: 14px; }
    .qopts { display: flex; flex-direction: column; gap: 6px; }
    .opt { display: flex; align-items: center; gap: 10px; text-align: left; width: 100%; padding: 9px 12px; border: 1px solid var(--border);
      border-radius: 10px; background: var(--surface); cursor: pointer; font-size: 13px; color: var(--text); }
    .opt:hover { border-color: var(--accent); }
    .opt.on { border-color: var(--accent); background: var(--accent-soft); }
    .radio { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--border); flex: none; position: relative; }
    .opt.on .radio { border-color: var(--accent); }
    .opt.on .radio::after { content: ''; position: absolute; inset: 2px; border-radius: 50%; background: var(--accent); }
    .olabel { flex: 1; }
    .rec { font-size: 10.5px; font-weight: 700; letter-spacing: .02em; color: var(--green); background: var(--green-soft);
      border: 1px solid color-mix(in srgb, var(--green) 35%, transparent); border-radius: 999px; padding: 1px 8px; flex: none; }
    .other { width: 100%; }
  `,
})
export class Choices {
  q = input.required<Choice>();
  value = model<string>('');
  showTitle = input(true);
  enter = output<void>();
  /** A typed answer that isn't one of the options. */
  protected typing = computed(() => { const v = this.value(); return !!v && !this.q().options.includes(v); });
}
