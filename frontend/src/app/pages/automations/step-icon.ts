import { Component, computed, input } from '@angular/core';

import { stepIcon } from '../../automations/describe';

/** Stroke icon for a step type (same visual language as the sidebar icons). */
@Component({
  selector: 'app-step-icon',
  template: `<svg viewBox="0 0 24 24" aria-hidden="true">@for (d of paths(); track $index) { <path [attr.d]="d" /> }</svg>`,
  styles: `
    :host { display: inline-grid; place-items: center; width: var(--size, 30px); height: var(--size, 30px); border-radius: 9px;
      background: var(--accent-soft); color: var(--accent); flex: none; }
    :host(.local) { background: var(--amber-soft); color: var(--amber); }
    :host(.control) { background: var(--grey-soft); color: var(--muted); }
    svg { width: 60%; height: 60%; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  `,
})
export class StepIcon {
  readonly type = input.required<string>();
  protected paths = computed(() => stepIcon(this.type()));
}
