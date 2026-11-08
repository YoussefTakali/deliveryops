import { Component, computed, inject, input, output, signal } from '@angular/core';

import { Auth } from './auth';

/** Change-password form (native accounts). Used by the forced first-login gate and by Mon compte.
 *  On success the backend revokes every other session and hands this browser a fresh one. */
@Component({
  selector: 'app-password-form',
  template: `
    <form class="pwf" (submit)="$event.preventDefault(); submit()">
      <div class="field">
        <label for="pw-current">{{ forced() ? 'Mot de passe temporaire' : 'Mot de passe actuel' }}</label>
        <input id="pw-current" [type]="show() ? 'text' : 'password'" autocomplete="current-password" required
               [value]="current()" (input)="current.set($any($event.target).value)" />
      </div>
      <div class="field">
        <label for="pw-next">Nouveau mot de passe</label>
        <input id="pw-next" [type]="show() ? 'text' : 'password'" autocomplete="new-password" required minlength="8"
               [value]="next()" (input)="next.set($any($event.target).value)" />
        @if (next()) {
          <div class="meter" [attr.data-level]="strength().level"><span [style.width.%]="strength().pct"></span></div>
          <div class="small hint">{{ strength().label }}</div>
        }
      </div>
      <div class="field">
        <label for="pw-confirm">Confirmer le nouveau mot de passe</label>
        <input id="pw-confirm" [type]="show() ? 'text' : 'password'" autocomplete="new-password" required
               [value]="confirm()" (input)="confirm.set($any($event.target).value)" />
      </div>
      <label class="showpw"><input type="checkbox" [checked]="show()" (change)="show.set($any($event.target).checked)" /> Afficher les mots de passe</label>

      @if (problem(); as p) { <p class="small err">{{ p }}</p> }
      @if (error()) { <p class="small err">{{ error() }}</p> }
      @if (success()) { <p class="small okmsg">Mot de passe modifié. Vos autres sessions ont été déconnectées.</p> }

      <div class="actions">
        <button class="primary" type="submit" [disabled]="busy() || !!problem() || !current() || !next()">
          @if (busy()) { <span class="spin"></span> } Changer le mot de passe
        </button>
      </div>
    </form>
  `,
  styles: `
    .pwf { display: flex; flex-direction: column; gap: 14px; text-align: left; }
    .field label { margin-bottom: 6px; }
    .meter { height: 5px; border-radius: 3px; background: var(--grey-soft); margin-top: 8px; overflow: hidden; }
    .meter span { display: block; height: 100%; background: var(--red); transition: width .2s ease; }
    .meter[data-level="2"] span { background: var(--amber); }
    .meter[data-level="3"] span { background: var(--green); }
    .hint { color: var(--muted); margin-top: 4px; }
    .showpw { display: flex; align-items: center; gap: 8px; margin: 0; cursor: pointer; }
    .showpw input { width: auto; }
    .err { color: var(--red); }
    .okmsg { color: var(--green); }
    .actions { display: flex; justify-content: flex-end; }
  `,
})
export class PasswordForm {
  private auth = inject(Auth);
  readonly forced = input(false);
  readonly done = output<void>();

  protected current = signal('');
  protected next = signal('');
  protected confirm = signal('');
  protected show = signal(false);
  protected busy = signal(false);
  protected error = signal('');
  protected success = signal(false);

  protected strength = computed(() => {
    const p = this.next();
    const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter(r => r.test(p)).length;
    const score = (p.length >= 8 ? 1 : 0) + (p.length >= 12 ? 1 : 0) + (classes >= 3 ? 1 : 0);
    const level = p.length < 8 ? 1 : Math.max(1, score);
    return { level, pct: Math.min(100, level * 34), label: p.length < 8 ? 'Au moins 8 caractères' : ['', 'Faible', 'Correct', 'Robuste'][level] };
  });

  protected problem = computed(() => {
    if (this.next() && this.next().length < 8) return 'Le nouveau mot de passe doit contenir au moins 8 caractères.';
    if (this.next() && this.next() === this.current()) return 'Le nouveau mot de passe doit être différent de l\'actuel.';
    if (this.confirm() && this.confirm() !== this.next()) return 'La confirmation ne correspond pas.';
    return '';
  });

  protected async submit() {
    if (this.problem() || this.confirm() !== this.next()) { this.error.set('La confirmation ne correspond pas.'); return; }
    this.busy.set(true);
    this.error.set('');
    this.success.set(false);
    try {
      await this.auth.changePassword(this.current(), this.next());
      this.current.set(''); this.next.set(''); this.confirm.set('');
      this.success.set(true);
      this.done.emit();
    } catch (e: any) {
      this.error.set(e?.error?.detail && typeof e.error.detail === 'string' ? e.error.detail : 'Impossible de changer le mot de passe.');
    } finally {
      this.busy.set(false);
    }
  }
}
