import { Component, computed, effect, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { Desktop } from '../../desktop';
import { AutomationApi, errorText } from '../../automations/automation-api';
import { AUTOMATION_STATUS, RUN_STATUS, formatWhen, triggerSummary } from '../../automations/describe';
import { dropNotes } from '../../automations/drafts';
import { Automation } from '../../automations/models';
import { AutomationRunner } from '../../automations/runner';
import { StepIcon } from './step-icon';

@Component({
  selector: 'app-automations-list',
  imports: [RouterLink, StepIcon],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Automatisations</h1>
          <p>Montrez ou décrivez une tâche répétitive une fois : l'IA en fait un enchaînement d'étapes que vous relisez, puis DeliveryOps l'exécute pour vous, à heure fixe ou à la réception d'un mail.</p>
        </div>
        <a class="btn primary" routerLink="/automations/new">+ Nouvelle automatisation</a>
      </div>

      @if (!runner.background) {
        <div class="notice">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.01"/></svg>
          <div>
            @if (desktop.available) {
              <strong>Mettez à jour l'application bureau</strong> pour exécuter les automatisations planifiées et les étapes locales.
            } @else {
              <strong>Dans un navigateur, rien ne s'exécute en arrière-plan.</strong>
              Les automatisations planifiées ou déclenchées par un mail ont besoin de l'application bureau DeliveryOps (ouverte ou dans la zone de notification).
              Ici, vous pouvez créer, tester à blanc et exécuter manuellement celles qui n'impriment pas et ne touchent pas aux fichiers du PC.
              <a routerLink="/download">Télécharger l'app bureau</a>
            }
          </div>
        </div>
      } @else {
        <div class="bg-row">
          <span class="small muted">
            @if (runner.activeCount()) { {{ runner.activeCount() }} automatisation(s) active(s) — fermer la fenêtre laisse DeliveryOps tourner dans la zone de notification. }
            @else { Les automatisations actives s'exécutent tant que l'application bureau est ouverte ou dans la zone de notification. }
          </span>
          @if (loginItemSupported()) {
            <label class="toggle">
              <input type="checkbox" role="switch" [checked]="launchAtLogin()" (change)="setLaunchAtLogin($any($event.target).checked)" />
              <span class="track" aria-hidden="true"><span class="thumb"></span></span>
              Lancer au démarrage de Windows
            </label>
          }
        </div>
      }

      @for (p of runner.pending(); track p.run.id) {
        <div class="pending">
          <div><strong>« {{ p.automation.name }} »</strong> attend votre confirmation
            <span class="small muted">— {{ p.run.trigger === 'mail' ? 'mail reçu' : 'exécution planifiée' }}</span></div>
          <div class="row">
            <button class="sm primary" (click)="confirm(p.run)">Exécuter</button>
            <button class="sm ghost" (click)="skip(p.run)">Ignorer</button>
          </div>
        </div>
      }

      @if (error()) {
        <div class="card empty-state">
          <p><strong>Impossible de charger les automatisations.</strong></p>
          <p class="small muted">{{ error() }}</p>
          <button class="sm" (click)="load()">Réessayer</button>
        </div>
      } @else if (loading() && !items().length) {
        <div class="empty"><span class="spin"></span></div>
      } @else if (!items().length) {
        <div class="card empty-state">
          <div class="hero-ic"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg></div>
          <h2>Aucune automatisation pour l'instant</h2>
          <p class="muted">Exemple : « Chaque lundi, un mail RH arrive avec la liste des stagiaires en Excel ; pour chacun, remplir la convention Word, l'imprimer et la lui envoyer par mail. »</p>
          <div class="row center">
            <a class="btn primary" routerLink="/automations/new" [queryParams]="{ tab: 'describe' }">Décrire une tâche</a>
            @if (desktop.available) { <a class="btn" routerLink="/automations/new" [queryParams]="{ tab: 'record' }">Enregistrer une tâche</a> }
          </div>
        </div>
      } @else {
        <div class="cards">
          @for (a of items(); track a.id) {
            <article class="card auto" [class.active]="a.status === 'active'">
              <div class="top">
                <a class="name" [routerLink]="['/automations', a.id]">{{ a.name }}</a>
                <span class="pill" [class]="'pill ' + statusOf(a).cls">{{ statusOf(a).label }}</span>
              </div>
              <div class="trigger small">{{ trigger(a) }}</div>
              <div class="icons">
                @for (s of a.steps.slice(0, 8); track s.id) { <app-step-icon [type]="s.type" [title]="s.title || s.type" /> }
                @if (a.steps.length > 8) { <span class="small faint">+{{ a.steps.length - 8 }}</span> }
              </div>
              <dl class="meta small">
                @if (a.status === 'active' && a.trigger.type === 'schedule') { <dt>Prochaine</dt><dd>{{ when(a.next_run_at) }}</dd> }
                <dt>Dernière</dt>
                <dd>
                  @if (a.last_run_status) { <span class="dot" [class]="'dot ' + runStatus(a.last_run_status).cls"></span> {{ runStatus(a.last_run_status).label }} · {{ when(a.last_run_at) }} }
                  @else { <span class="faint">jamais exécutée</span> }
                </dd>
              </dl>
              @if (liveFor(a.id); as lr) {
                <div class="live small">
                  <span class="spin"></span> {{ lr.dryRun ? 'Test à blanc' : 'Exécution' }} — étape {{ lr.stepIndex }}/{{ lr.stepTotal }}
                  @if (lr.iteration) { · {{ lr.iteration.i }}/{{ lr.iteration.n }} }
                </div>
              }
              @if (feedback()[a.id]; as fb) { <div class="small fb" [class.err]="fb.err">{{ fb.text }}</div> }
              <div class="actions">
                @if (confirmDelete() === a.id) {
                  <span class="small">Supprimer définitivement ?</span>
                  <button class="sm ko" (click)="remove(a)">Supprimer</button>
                  <button class="sm ghost" (click)="confirmDelete.set(null)">Annuler</button>
                } @else {
                  <button class="sm primary" [disabled]="!!blocked(a) || busy()[a.id]" [title]="blocked(a) ?? ''" (click)="run(a, false)">Exécuter</button>
                  <button class="sm" [disabled]="!!blocked(a) || busy()[a.id]" [title]="blocked(a) ?? ''" (click)="run(a, true)">Test à blanc</button>
                  @if (a.status === 'active') { <button class="sm" [disabled]="busy()[a.id]" (click)="toggle(a)">Pause</button> }
                  @else { <button class="sm" [disabled]="busy()[a.id]" (click)="toggle(a)">Activer</button> }
                  <span class="spacer"></span>
                  <a class="btn sm ghost" [routerLink]="['/automations', a.id]">Modifier</a>
                  <button class="sm ghost" (click)="confirmDelete.set(a.id)" aria-label="Supprimer">Supprimer</button>
                }
              </div>
            </article>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .btn.primary:hover { text-decoration: none; filter: brightness(1.08); }
    .btn:hover { text-decoration: none; }
    .notice { display: flex; gap: 10px; align-items: flex-start; background: var(--amber-soft); color: var(--text); border-radius: var(--radius);
      padding: 12px 14px; margin-bottom: 16px; font-size: 13px; line-height: 1.5; }
    .notice svg { width: 18px; height: 18px; flex: none; fill: none; stroke: var(--amber); stroke-width: 2; stroke-linecap: round; margin-top: 1px; }
    .bg-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
    .pending { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 10px 14px; margin-bottom: 10px;
      border-radius: var(--radius); border: 1px solid color-mix(in srgb, var(--amber) 40%, var(--border)); background: var(--surface); animation: rise .2s ease; }
    .empty-state { text-align: center; padding: 36px 24px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
    .empty-state p { max-width: 620px; }
    .hero-ic { width: 48px; height: 48px; border-radius: 14px; background: var(--accent-soft); color: var(--accent); display: grid; place-items: center; }
    .hero-ic svg { width: 26px; height: 26px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linejoin: round; }
    .center { justify-content: center; margin-top: 6px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
    .auto { display: flex; flex-direction: column; gap: 10px; animation: rise .22s ease both; transition: border-color .15s ease, box-shadow .15s ease; }
    .auto:hover { border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); }
    .auto.active { border-left: 3px solid var(--green); }
    .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .name { font-weight: 600; font-size: 15px; color: var(--text); line-height: 1.3; }
    .trigger { color: var(--muted); }
    .icons { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; --size: 26px; }
    .meta { display: grid; grid-template-columns: auto 1fr; gap: 3px 12px; margin: 0; }
    .meta dt { color: var(--faint); }
    .meta dd { margin: 0; display: flex; align-items: center; gap: 6px; }
    .live { display: flex; align-items: center; gap: 8px; color: var(--accent); background: var(--accent-soft); border-radius: 8px; padding: 6px 10px; }
    .fb { color: var(--green); } .fb.err { color: var(--red); }
    .actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; border-top: 1px solid var(--border); padding-top: 10px; margin-top: auto; }
    .toggle { display: inline-flex; align-items: center; gap: 8px; margin: 0; font-size: 13px; color: var(--text); cursor: pointer; }
    .toggle input { position: absolute; opacity: 0; width: 1px; height: 1px; }
    .track { width: 30px; height: 17px; border-radius: 999px; background: var(--border); position: relative; transition: background-color .2s ease; flex: none; }
    .thumb { position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.3); transition: transform .2s ease; }
    .toggle input:checked + .track { background: var(--accent); }
    .toggle input:checked + .track .thumb { transform: translateX(13px); }
    .toggle input:focus-visible + .track { outline: 2px solid var(--accent); outline-offset: 2px; }
    @keyframes rise { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
    @media (prefers-reduced-motion: reduce) { .auto, .pending { animation: none; } }
  `,
})
export class AutomationsListPage {
  private api = inject(AutomationApi);
  private router = inject(Router);
  protected desktop = inject(Desktop);
  protected runner = inject(AutomationRunner);

  protected items = signal<Automation[]>([]);
  protected loading = signal(true);
  protected error = signal('');
  protected busy = signal<Record<string, boolean>>({});
  protected feedback = signal<Record<string, { text: string; err: boolean }>>({});
  protected confirmDelete = signal<string | null>(null);
  protected launchAtLogin = signal(false);
  protected loginItemSupported = signal(false);
  private liveByAutomation = computed(() => {
    const m: Record<string, any> = {};
    for (const lr of this.runner.liveList()) if (lr.status === 'running') m[lr.automationId] = lr;
    return m;
  });

  constructor() {
    void this.runner.ensureCatalog().catch(() => {});
    effect(() => { this.runner.version(); void this.load(); });
    const bridge = this.desktop.automation;
    if (bridge) {
      bridge.getLaunchAtLogin().then(s => { this.launchAtLogin.set(s.openAtLogin); this.loginItemSupported.set(s.supported); }).catch(() => {});
    }
  }

  async load() {
    this.loading.set(true);
    try {
      const list = await this.api.list();
      list.sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
      this.items.set(list);
      this.error.set('');
      void this.runner.refreshActiveCount(list);
    } catch (err: any) {
      this.error.set(err?.status === 404 ? 'Le serveur ne propose pas encore les automatisations (mise à jour du backend nécessaire).' : errorText(err));
    } finally {
      this.loading.set(false);
    }
  }

  protected trigger(a: Automation) { return triggerSummary(a.trigger); }
  protected when(iso?: string | null) { return formatWhen(iso); }
  protected statusOf(a: Automation) { return AUTOMATION_STATUS[a.status] ?? AUTOMATION_STATUS.draft; }
  protected runStatus(s: string) { return RUN_STATUS[s as keyof typeof RUN_STATUS] ?? { label: s, cls: '' }; }
  protected liveFor(id: string) { return this.liveByAutomation()[id] ?? null; }
  protected blocked(a: Automation) { return this.runner.cannotRunReason(a); }

  private setBusy(id: string, v: boolean) { this.busy.update(b => ({ ...b, [id]: v })); }
  private say(id: string, text: string, err = false) {
    this.feedback.update(f => ({ ...f, [id]: { text, err } }));
    setTimeout(() => this.feedback.update(f => { const { [id]: _, ...rest } = f; return rest; }), 8000);
  }

  protected async run(a: Automation, dryRun: boolean) {
    this.setBusy(a.id, true);
    try {
      const { done } = await this.runner.runNow(a, dryRun);
      this.say(a.id, dryRun ? 'Test à blanc lancé…' : 'Exécution lancée…');
      void done.then(status => this.say(a.id, status === 'succeeded' ? (dryRun ? 'Test à blanc réussi.' : 'Exécution réussie.') : `Terminé : ${this.runStatus(status).label}.`, status !== 'succeeded'));
    } catch (err) {
      this.say(a.id, errorText(err), true);
    } finally {
      this.setBusy(a.id, false);
    }
  }

  protected async toggle(a: Automation) {
    this.setBusy(a.id, true);
    try {
      const updated = a.status === 'active' ? await this.api.pause(a.id) : await this.api.activate(a.id);
      this.items.update(list => list.map(x => (x.id === a.id ? { ...x, ...updated } : x)));
      void this.runner.refreshActiveCount(this.items());
      this.say(a.id, a.status === 'active' ? 'Mise en pause.' : 'Activée.');
    } catch (err) {
      this.say(a.id, a.status === 'active' ? errorText(err) : `Activation impossible : ${errorText(err)}. Ouvrez l'automatisation pour corriger.`, true);
    } finally {
      this.setBusy(a.id, false);
    }
  }

  protected async remove(a: Automation) {
    this.confirmDelete.set(null);
    try {
      await this.api.remove(a.id);
      dropNotes(a.id);
      this.items.update(list => list.filter(x => x.id !== a.id));
      void this.runner.refreshActiveCount(this.items());
    } catch (err) { this.say(a.id, errorText(err), true); }
  }

  protected async setLaunchAtLogin(on: boolean) {
    try { this.launchAtLogin.set((await this.desktop.automation!.setLaunchAtLogin(on)).openAtLogin); }
    catch { this.launchAtLogin.set(!on); }
  }

  protected async confirm(run: any) {
    try { await this.runner.confirmAndRun(run); } catch (err) { this.error.set(errorText(err)); }
  }
  protected async skip(run: any) {
    try { await this.runner.skipRun(run); } catch (err) { this.error.set(errorText(err)); }
  }
}
