import { Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { AutomationApi } from '../automations/automation-api';
import { AutomationRunner, LiveRun } from '../automations/runner';
import { seedRecommended } from '../choices';
import { ChatMessage, Companion, Memory, Suggestion, TaskPlan, TaskQuestion } from '../companion.service';
import { errorText } from '../http-error';
import { Desktop } from '../desktop';
import { renderMd } from '../shared';

/** State of an in-chat agentic task (plan → questions → approve → run). */
interface TaskState {
  message: string;
  phase: 'planning' | 'questions' | 'plan' | 'running';
  questions: TaskQuestion[];
  answers: Record<string, string>;
  plan: Extract<TaskPlan, { status: 'plan' }> | null;
  automationId?: string;
  runId?: string;
  error?: string;
}

const ROLES: { id: string; label: string; hint: string }[] = [
  { id: 'employee', label: 'Collaborateur', hint: 'Organisation, recherche d’information, rédaction' },
  { id: 'consultant', label: 'Consultant', hint: 'Livrables client, comptes rendus, propositions' },
  { id: 'manager', label: 'Manager', hint: 'Suivi d’équipe, 1:1, comités, reporting' },
  { id: 'hr', label: 'RH', hint: 'Recrutement, onboarding, entretiens — discrétion renforcée' },
  { id: 'executive', label: 'Dirigeant', hint: 'Synthèses courtes, décisions, risques' },
  { id: 'assistant', label: 'Assistant(e) de direction', hint: 'Agenda, dossiers, relances' },
];
const TONES = [{ id: 'warm', label: 'Chaleureux' }, { id: 'neutral', label: 'Professionnel' }, { id: 'direct', label: 'Direct' }];
const KINDS: Record<string, string> = { preference: 'Préférence', fact: 'Fait', routine: 'Habitude', person: 'Personne', project: 'Projet' };
const CATS: Record<string, string> = {
  meeting: 'Réunions', mail: 'Mails', documents: 'Documents', spreadsheets: 'Tableurs', slides: 'Présentations', code: 'Code',
  chat: 'Messagerie', browser: 'Navigation', files: 'Fichiers', planning: 'Planification', other: 'Autre',
};

/** "Compagnon" — the user's home: what's going on now, what the companion offers, a conversation
 *  that knows their context, and full control over what it knows and collects. */
@Component({
  selector: 'app-companion',
  imports: [RouterLink],
  template: `
    <div class="page">
      @if (c.state(); as s) {
        @if (!s.policy.enabled) {
          <div class="card"><h2>Compagnon désactivé</h2><p class="muted">Votre organisation a désactivé le compagnon de travail.</p></div>
        } @else if (!s.profile.onboarded) {
          <!-- First run: who are you, how should I talk to you, and what may I observe -->
          <div class="card welcome">
            <h1>Bonjour {{ s.profile.display_name }} <svg class="ic spark" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z"/><path d="M18.5 4l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7z"/></svg></h1>
            <p class="lead">Je suis votre compagnon de travail. Je connais vos mails, votre agenda et vos fichiers indexés, et — si vous le voulez —
              ce sur quoi vous travaillez en ce moment, pour vous proposer de l'aide au bon moment. J'apprends vos préférences au fil du temps,
              et vous gardez la main sur tout ce que je sais.</p>
            <h3>Votre métier</h3>
            <div class="choices">
              @for (r of roles; track r.id) {
                <button class="choice" [class.on]="draftRole() === r.id" (click)="draftRole.set(r.id)"><strong>{{ r.label }}</strong><span>{{ r.hint }}</span></button>
              }
            </div>
            <h3>Comment je vous parle</h3>
            <div class="row gap">
              @for (t of tones; track t.id) { <button class="chip" [class.on]="draftTone() === t.id" (click)="draftTone.set(t.id)">{{ t.label }}</button> }
            </div>
            @if (s.can_track && s.policy.activity_tracking_allowed) {
              <h3>Ce que je peux observer</h3>
              <label class="consent">
                <input type="checkbox" [checked]="draftTracking()" (change)="draftTracking.set($any($event.target).checked)" />
                <span><strong>Savoir sur quoi je travaille</strong> — uniquement l'application et le titre de la fenêtre active
                  {{ s.policy.title_capture === 'app_only' ? '(votre organisation limite la collecte au nom de l’application)' : '' }}.
                  Jamais de frappes clavier ni de contenu d'écran ; rien pendant les pauses ; gestionnaires de mots de passe, banque, navigation privée
                  et paie sont toujours ignorés ; conservé {{ s.policy.retention_days }} jours ; vos administrateurs ne voient que des statistiques agrégées.
                  @if (!desktop.available) { <em>Nécessite l'application bureau.</em> }
                </span>
              </label>
            }
            <div class="row end"><button class="primary" (click)="finishOnboarding()">Commencer</button></div>
          </div>
        } @else {
          <div class="page-head">
            <div>
              <h1>{{ greeting() }}, {{ s.profile.display_name }}</h1>
              <p>
                @if (s.today?.now; as now) { Vous êtes sur <strong>{{ now.title || now.app }}</strong> depuis {{ mins(now.seconds) }}. }
                @else if (s.tracking_active) { Je garde un œil discret sur votre journée. }
                @else { Je suis là quand vous avez besoin de moi. }
              </p>
            </div>
            <div class="head-actions">
              @if (s.tracking_active) { <span class="pill live" [class.off]="!c.trackerRunning()">{{ c.trackerRunning() ? 'Suivi actif' : 'Suivi en attente (app bureau)' }}</span> }
              <button class="sm" (click)="brief('morning')" [disabled]="busy()">Brief du jour</button>
              <button class="sm" (click)="brief('evening')" [disabled]="busy()">Bilan</button>
            </div>
          </div>

          <div class="layout">
            <div class="main-col">
              <!-- Agentic task: plan → questions → approve → run, inline -->
              @if (task(); as t) {
                <div class="card taskcard">
                  <div class="row between"><strong><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg> {{ t.message }}</strong><button class="icon" (click)="cancelTask()" title="Fermer" aria-label="Fermer"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
                  @if (t.error) {
                    <div class="row gap errline"><p class="small warn">{{ t.error }}</p>
                      <button class="sm" (click)="debugTask(t.error)"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 9h.01M15 9h.01M8 14c1.2 1.3 2.5 2 4 2s2.8-.7 4-2"/><circle cx="12" cy="12" r="8.5"/></svg> Déboguer avec l'IA</button></div>
                  }
                  @switch (t.phase) {
                    @case ('planning') { <p class="small muted"><span class="spin"></span> Je prépare un plan…</p> }
                    @case ('questions') {
                      @let q = t.questions[taskStep()];
                      <p class="small muted">Question {{ taskStep() + 1 }} sur {{ t.questions.length }}</p>
                      @if (q) {
                        <div class="qblock">
                          <div class="qtitle">{{ q.question }}</div>
                          @if (q.options.length) {
                            <div class="qopts">
                              @for (o of q.options; track o) {
                                <button type="button" class="opt" [class.on]="optionOn(q, o)" (click)="chooseOption(q, o)">
                                  <span class="radio"></span><span class="optlabel">{{ o }}</span>
                                  @if (o === q.recommended) { <span class="rec" title="L'option recommandée par l'IA">Recommandé</span> }
                                </button>
                              }
                            </div>
                          }
                          <input class="otherin" [placeholder]="q.options.length ? 'Autre — écrivez votre réponse' : 'Votre réponse'"
                                 [value]="otherText(q)" (input)="onOther(q, $any($event.target).value)" (keydown.enter)="nextQ()" />
                        </div>
                      }
                      <div class="row end gap">
                        <button class="sm" (click)="cancelTask()">Annuler</button>
                        @if (taskStep() > 0) { <button class="sm" (click)="prevQ()">Précédent</button> }
                        <button class="primary sm" (click)="nextQ()" [disabled]="!curAnswered()">{{ taskStep() < t.questions.length - 1 ? 'Suivant' : 'Continuer' }}</button>
                      </div>
                    }
                    @case ('plan') {
                      @if (t.plan; as p) {
                        @if (p.explanation) { <p class="small">{{ p.explanation }}</p> }
                        <ol class="plan">
                          @for (s of p.steps; track s.id) {
                            <li [class.gate]="s.confirm">{{ s.confirm ? '⏸ Je vous demanderai avant l’étape suivante' : s.title }}</li>
                          }
                        </ol>
                        @if (p.scripts.length) {
                          <details><summary class="small">Commandes exactes à approuver ({{ p.scripts.length }})</summary>
                            @for (sc of p.scripts; track sc.step_id) { <pre class="script">{{ sc.script }}</pre> }
                          </details>
                        }
                        @for (w of p.warnings; track w) { <p class="small warn">{{ w }}</p> }
                        <div class="row end gap">
                          <button class="sm" (click)="cancelTask()">Annuler</button>
                          <button class="primary sm" [disabled]="p.blocking || !desktop.available" (click)="runPlan()">Approuver et exécuter</button>
                        </div>
                        @if (!desktop.available) { <p class="small faint">Ouvrez l'application bureau pour exécuter la tâche.</p> }
                      }
                    }
                    @case ('running') {
                      @if (liveTask(); as lr) {
                        <p class="small">@if (lr.status === 'running') { <span class="spin"></span> } {{ lr.stepTitle || 'Exécution…' }} <span class="faint">({{ lr.stepIndex }}/{{ lr.stepTotal }})</span></p>
                        @if (lr.confirm; as cf) {
                          <div class="confirm"><span class="small">{{ cf.message }}</span>
                            <button class="primary sm" (click)="answerConfirm(true)">Continuer</button>
                            <button class="sm" (click)="answerConfirm(false)">Annuler</button></div>
                        }
                        <div class="runlog">@for (e of lr.log.slice(-8); track $index) { <div class="small" [class.warn]="e.level === 'error'">{{ e.message }}</div> }</div>
                        @if (lr.status !== 'running') {
                          <p class="small resultline" [class.ok]="lr.status === 'succeeded'" [class.warn]="lr.status !== 'succeeded'">
                            @if (lr.status === 'succeeded') { <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/></svg> Tâche terminée }
                            @else if (lr.status === 'cancelled') { <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 8v5M12 16h.01"/></svg> Annulée }
                            @else { <svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M9 9l6 6M15 9l-6 6"/></svg> Échec }
                            {{ lr.error ? ' — ' + lr.error : '' }}
                          </p>
                          <div class="row end gap">
                            @if (lr.status === 'failed') { <button class="sm" (click)="debugTask(lr.error || 'la tâche a échoué', lr.log)">Déboguer avec l'IA</button> }
                            <button class="sm" (click)="cancelTask()">Fermer</button>
                          </div>
                        }
                      } @else { <p class="small muted"><span class="spin"></span> Démarrage…</p> }
                    }
                  }
                </div>
              }

              <!-- Proactive offers -->
              @if (s.suggestions.length) {
                <div class="stack offers">
                  @for (o of s.suggestions; track o.id) {
                    <div class="card offer">
                      <div class="offer-body"><span class="pill">{{ o.label }}</span><strong>{{ o.title }}</strong><p class="small muted">{{ o.body }}</p></div>
                      <div class="offer-actions">
                        @if (o.action.type !== 'none') { <button class="primary sm" (click)="accept(o)">Oui, merci</button> }
                        <button class="sm" (click)="c.decide(o, 'dismissed')">Pas maintenant</button>
                        <button class="sm ghost" (click)="c.decide(o, 'never')" title="Ne plus jamais me proposer ce type d'aide">Jamais</button>
                      </div>
                    </div>
                  }
                </div>
              }

              <!-- Conversation -->
              <div class="card chat">
                <div class="feed" #feed>
                  @for (m of messages(); track $index) {
                    <div class="msg" [class.me]="m.role === 'user'">
                      <div class="md" [innerHTML]="md(m.content)"></div>@if (m.streaming) { <span class="caret"></span> }
                      @if (m.sources?.length && !m.streaming) {
                        <div class="sources">@for (src of m.sources!.slice(0, 5); track $index) { <span class="src-chip" [title]="src.excerpt">{{ src.label }}</span> }</div>
                      }
                    </div>
                  } @empty {
                    <div class="empty">
                      <p class="muted">Demandez-moi n'importe quoi sur votre travail — ou dites-moi ce que je dois retenir.</p>
                      <div class="row gap wrap">
                        @for (q of starters; track q) { <button class="chip" (click)="send(q)">{{ q }}</button> }
                      </div>
                    </div>
                  }
                </div>
                <form class="row composer" (submit)="$event.preventDefault(); send(draft())">
                  <input [value]="draft()" (input)="draft.set($any($event.target).value)" placeholder="Écrivez à votre compagnon…  (ex. « installe Python 3.12 », « Souviens-toi que… »)" />
                  <button type="button" class="do" [disabled]="!draft().trim() || !!task()" (click)="doTask()" title="Réaliser cette tâche sur mon PC (installer, configurer, télécharger…)"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg> Faire</button>
                  <button class="primary" [disabled]="busy() || !draft().trim()">@if (busy()) { <span class="spin"></span> } Envoyer</button>
                </form>
                <p class="small faint">« Faire » = je réalise la tâche : je pose les questions utiles, je vous montre les commandes exactes, et je n'exécute qu'après votre accord.</p>
                @if (learned()) { <p class="small faint"><svg class="ic spark" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z"/><path d="M18.5 4l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7z"/></svg> J'ai retenu {{ learned() }} nouvelle(s) chose(s) sur vous — voyez « Ce que je sais de vous ».</p> }
                @if (!s.llm) { <p class="small faint">IA hors ligne : je réponds à partir de vos données indexées uniquement.</p> }
              </div>
            </div>

            <div class="side-col">
              <!-- Today -->
              @if (s.agenda.length) {
                <div class="card">
                  <h3>À venir</h3>
                  @for (e of s.agenda; track $index) {
                    <div class="ev"><span class="when">{{ time(e.start) }}</span><span><strong>{{ e.subject }}</strong>@if (e.attendees.length) { <span class="small muted"> · {{ e.attendees.slice(0, 3).join(', ') }}</span> }</span></div>
                  }
                </div>
              }
              @if (s.today; as d) {
                <div class="card">
                  <h3>Votre journée <span class="small muted">· {{ mins(d.tracked_seconds) }}</span></h3>
                  @for (cat of d.by_category.slice(0, 6); track cat.category) {
                    <div class="bar-row"><span>{{ catLabel(cat.category) }}</span><div class="bar"><span [style.width.%]="pct(cat.seconds, d.tracked_seconds)"></span></div><span class="small muted">{{ mins(cat.seconds) }}</span></div>
                  } @empty { <p class="small muted">Rien d'enregistré pour l'instant aujourd'hui.</p> }
                </div>
              }

              <!-- Memory -->
              <div class="card">
                <h3>Ce que je sais de vous <span class="small muted">· {{ memories().length }}</span></h3>
                <form class="row" (submit)="$event.preventDefault(); addMemory()">
                  <input [value]="newMemory()" (input)="newMemory.set($any($event.target).value)" placeholder="Ajouter : « Mon manager est… »" />
                  <button class="sm">Ajouter</button>
                </form>
                <div class="mems">
                  @for (m of memories(); track m.id) {
                    <div class="mem" [class.pinned]="m.pinned">
                      <span class="pill">{{ kindLabel(m.kind) }}</span>
                      <span class="mem-text">{{ m.content }}@if (m.source === 'learned') { <em class="small faint"> · appris</em> }</span>
                      <button class="icon" (click)="togglePin(m)" [title]="m.pinned ? 'Désépingler' : 'Épingler (toujours pris en compte)'" [attr.aria-pressed]="m.pinned">
                        <svg class="ic" viewBox="0 0 24 24" [style.fill]="m.pinned ? 'currentColor' : 'none'" [style.color]="m.pinned ? 'var(--accent)' : ''" aria-hidden="true"><path d="M12 4l2.3 4.7 5.2.8-3.8 3.7.9 5.1L12 16.9l-4.6 2.4.9-5.1L4.5 9.5l5.2-.8z"/></svg></button>
                      <button class="icon" (click)="removeMemory(m)" title="Oublier" aria-label="Oublier"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
                    </div>
                  } @empty { <p class="small muted">Rien encore. Je retiens ce que vous me dites de retenir, et j'apprends de nos échanges.</p> }
                </div>
              </div>

              <!-- Preferences & privacy -->
              <div class="card">
                <button class="fold" (click)="prefsOpen.set(!prefsOpen())">Préférences & confidentialité <span>{{ prefsOpen() ? '−' : '+' }}</span></button>
                @if (prefsOpen()) {
                  <div class="prefs">
                    <label>Métier
                      <select [value]="s.profile.job_role" (change)="save({ job_role: $any($event.target).value })">@for (r of roles; track r.id) { <option [value]="r.id">{{ r.label }}</option> }</select>
                    </label>
                    <label>Ton
                      <select [value]="s.profile.tone" (change)="save({ tone: $any($event.target).value })">@for (t of tones; track t.id) { <option [value]="t.id">{{ t.label }}</option> }</select>
                    </label>
                    <label>Langue
                      <select [value]="s.profile.language" (change)="save({ language: $any($event.target).value })"><option value="">Automatique</option><option value="fr">Français</option><option value="en">English</option></select>
                    </label>
                    <label>Ne pas me déranger
                      <span class="row gap"><input type="time" [value]="s.profile.quiet_start" (change)="save({ quiet_start: $any($event.target).value })" /> → <input type="time" [value]="s.profile.quiet_end" (change)="save({ quiet_end: $any($event.target).value })" /></span>
                    </label>
                    <label class="check"><input type="checkbox" [checked]="s.profile.proactive_enabled" (change)="save({ proactive_enabled: $any($event.target).checked })" /> Me proposer de l'aide spontanément</label>
                    @if (s.can_track && s.policy.activity_tracking_allowed) {
                      <label class="check"><input type="checkbox" [checked]="s.profile.tracking_enabled" (change)="save({ tracking_enabled: $any($event.target).checked })" /> Savoir sur quoi je travaille (application + titre de fenêtre)</label>
                    }
                    @if (bg(); as b) {
                      <label class="check"><input type="checkbox" [checked]="b.closeToTray" (change)="setBg({ closeToTray: $any($event.target).checked })" /> Rester en arrière-plan quand je ferme la fenêtre (zone de notification)</label>
                      @if (b.supported) { <label class="check"><input type="checkbox" [checked]="b.openAtLogin" (change)="setBg({ launchAtLogin: $any($event.target).checked })" /> Lancer au démarrage de Windows</label> }
                    }
                    <h4>Types d'aide <span class="small muted">— s'ajustent selon vos réponses</span></h4>
                    @for (n of s.profile.nudges; track n.kind) {
                      <div class="nudge"><span>{{ n.label }}</span>
                        <input type="range" min="0" max="2" step="0.25" [value]="n.weight" (change)="setWeight(n.kind, $any($event.target).value)" />
                        <span class="small muted">{{ n.weight < 0.3 ? 'jamais' : n.weight > 1.2 ? 'souvent' : 'normal' }}</span></div>
                    }
                    <h4>Mes données</h4>
                    <div class="row gap wrap">
                      <button class="sm" (click)="exportData()">Exporter tout (JSON)</button>
                      <button class="sm" (click)="erase('activity')">Effacer mon activité</button>
                      <button class="sm" (click)="erase('messages')">Effacer la conversation</button>
                      <button class="sm danger" (click)="erase('all')">Tout effacer</button>
                    </div>
                    <p class="small faint">Vos administrateurs ne voient que des statistiques agrégées, jamais vos titres de fenêtres, vos mémoires ni vos échanges.
                      <a routerLink="/workspace">Gérer les données indexées →</a></p>
                  </div>
                }
              </div>
            </div>
          </div>
        }
      } @else if (c.loadError(); as err) {
        <div class="card load-error">
          <h2>Compagnon indisponible</h2>
          <p class="muted">{{ err }}</p>
          <div class="row end"><button class="primary" (click)="c.retry()">Réessayer</button></div>
        </div>
      } @else {
        <div class="card"><span class="spin"></span> Chargement…</div>
      }
    </div>
  `,
  styles: `
    .taskcard { border-left: 3px solid var(--accent); display: flex; flex-direction: column; gap: 8px; }
    .row.between { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    .taskcard .qblock { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
    .taskcard .qtitle { font-weight: 600; font-size: 13.5px; }
    .taskcard .qopts { display: flex; flex-direction: column; gap: 6px; }
    .taskcard .opt { display: flex; align-items: center; gap: 10px; text-align: left; width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); cursor: pointer; font-size: 13px; color: var(--text); }
    .taskcard .opt:hover { border-color: var(--accent); }
    .taskcard .opt.on { border-color: var(--accent); background: var(--accent-soft); }
    .taskcard .opt .radio { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--border); flex: none; position: relative; }
    .taskcard .opt.on .radio { border-color: var(--accent); }
    .taskcard .opt.on .radio::after { content: ''; position: absolute; inset: 2px; border-radius: 50%; background: var(--accent); }
    .taskcard .otherin { width: 100%; margin-top: 2px; }
    .taskcard .optlabel { flex: 1; }
    .rec { font-size: 10.5px; font-weight: 700; color: var(--green); background: var(--green-soft); border: 1px solid color-mix(in srgb, var(--green) 35%, transparent); border-radius: 999px; padding: 1px 8px; flex: none; }
    .errline { align-items: center; flex-wrap: wrap; } .errline p { margin: 0; }
    .taskcard .plan { margin: 4px 0; padding-left: 20px; display: flex; flex-direction: column; gap: 3px; font-size: 13px; }
    .taskcard .plan li.gate { color: var(--muted); font-style: italic; list-style: none; margin-left: -14px; }
    .taskcard .script { background: var(--surface-2); padding: 8px 10px; border-radius: 8px; font-size: 12px; white-space: pre-wrap; word-break: break-word; overflow-x: auto; }
    .taskcard .confirm { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px; border: 1px solid var(--accent); border-radius: 8px; }
    .taskcard .runlog { max-height: 160px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; background: var(--surface-2); padding: 8px; border-radius: 8px; }
    .small.ok { color: var(--green, #2e9d5b); }
    button.do { background: var(--surface); border: 1px solid var(--accent); color: var(--accent); border-radius: 8px; padding: 0 10px; cursor: pointer; }
    button.do:disabled { opacity: .5; cursor: default; }
    .ic { width: 16px; height: 16px; flex: none; vertical-align: -3px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .ic.spark { stroke-width: 1.6; color: var(--accent); }
    h1 .ic.spark { width: 20px; height: 20px; vertical-align: -3px; }
    .resultline { display: flex; align-items: center; gap: 6px; }
    .icon .ic { width: 15px; height: 15px; vertical-align: -2px; }
    .do .ic { width: 15px; height: 15px; vertical-align: -3px; }
    .load-error { max-width: 520px; margin: 48px auto; text-align: center; }
    .load-error .row.end { justify-content: center; margin-top: 16px; }
    .welcome { max-width: 860px; margin: 24px auto; padding: 28px; }
    .welcome h1 { margin: 0 0 8px; } .lead { color: var(--muted); line-height: 1.55; }
    .welcome h3 { margin: 22px 0 10px; font-size: 14px; }
    .choices { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
    .choice { text-align: left; display: flex; flex-direction: column; gap: 3px; padding: 12px; border-radius: 10px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; }
    .choice span { font-size: 12px; color: var(--muted); } .choice.on { border-color: var(--accent); background: var(--accent-soft); }
    .chip { padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); cursor: pointer; font-size: 13px; }
    .chip.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
    .consent { display: flex; gap: 10px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 10px; font-size: 13px; line-height: 1.5; color: var(--muted); }
    .consent input { width: auto; flex: none; margin-top: 3px; }
    .consent strong { color: var(--text); } .row.end { justify-content: flex-end; margin-top: 20px; } .row.gap { gap: 8px; align-items: center; } .wrap { flex-wrap: wrap; }
    .head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .pill.live { color: var(--green, #2e9d5b); border-color: currentColor; } .pill.live.off { color: var(--muted); }
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; align-items: start; }
    @media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
    .main-col, .side-col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
    .offer { display: flex; gap: 14px; align-items: center; justify-content: space-between; border-left: 3px solid var(--accent); flex-wrap: wrap; }
    .offer-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; } .offer-body p { margin: 0; } .offer-body .pill { align-self: flex-start; }
    .offer-actions { display: flex; gap: 6px; flex-wrap: wrap; } button.ghost { background: transparent; border-color: transparent; color: var(--muted); }
    .chat { display: flex; flex-direction: column; gap: 10px; }
    .feed { min-height: 300px; max-height: 56vh; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding: 4px; }
    .msg { max-width: 86%; padding: 10px 13px; border-radius: 14px; background: var(--surface-2); align-self: flex-start; }
    .msg.me { align-self: flex-end; background: var(--accent-soft); } .msg p { margin: 0; line-height: 1.5; }
    .pre { white-space: pre-wrap; word-break: break-word; }
    .md { word-break: break-word; }
    .sources { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
    .src-chip { font-size: 11px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty { margin: auto; text-align: center; display: flex; flex-direction: column; gap: 12px; align-items: center; padding: 24px; } .empty .row { justify-content: center; }
    .composer input { flex: 1; }
    .caret { display: inline-block; width: 7px; height: 1em; margin-left: 2px; background: var(--accent); vertical-align: text-bottom; animation: blink 1s steps(2) infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    .side-col h3 { margin: 0 0 10px; font-size: 14px; } h4 { margin: 14px 0 6px; font-size: 13px; }
    .ev { display: flex; gap: 10px; padding: 5px 0; font-size: 13px; } .when { color: var(--accent); font-weight: 600; min-width: 46px; }
    .bar-row { display: grid; grid-template-columns: 96px 1fr 52px; gap: 8px; align-items: center; font-size: 12px; padding: 3px 0; }
    .bar { height: 7px; border-radius: 4px; background: var(--surface-2); overflow: hidden; } .bar span { display: block; height: 100%; background: var(--accent); border-radius: 4px; }
    .mems { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; max-height: 300px; overflow-y: auto; }
    .mem { display: flex; gap: 7px; align-items: flex-start; font-size: 12.5px; padding: 6px 8px; border-radius: 8px; background: var(--surface-2); }
    .mem.pinned { outline: 1px solid var(--accent); } .mem-text { flex: 1; line-height: 1.4; }
    .icon { border: none; background: none; cursor: pointer; color: var(--muted); padding: 0 3px; font-size: 14px; } .icon:hover { color: var(--text); }
    .fold { width: 100%; display: flex; justify-content: space-between; background: none; border: none; font-weight: 600; font-size: 14px; cursor: pointer; color: var(--text); padding: 0; }
    .prefs { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
    .prefs label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); } .prefs label.check { flex-direction: row; align-items: center; gap: 8px; color: var(--text); font-size: 13px; }
    .nudge { display: grid; grid-template-columns: 1fr 90px 48px; gap: 8px; align-items: center; font-size: 12px; }
    button.danger { color: var(--red, #c0392b); border-color: currentColor; }
  `,
})
export class CompanionPage {
  protected md = renderMd;
  protected c = inject(Companion);
  protected desktop = inject(Desktop);
  private api = inject(AutomationApi);
  private runner = inject(AutomationRunner);
  private feed = viewChild<ElementRef<HTMLElement>>('feed');

  protected roles = ROLES;
  protected tones = TONES;
  protected starters = ['Qu’est-ce qui demande mon attention aujourd’hui ?', 'Résume mes derniers mails importants', 'Aide-moi à préparer ma prochaine réunion', 'Sur quoi ai-je passé ma journée ?'];
  protected draftRole = signal('employee');
  protected draftTone = signal('warm');
  protected draftTracking = signal(false);
  protected messages = signal<ChatMessage[]>([]);
  protected memories = signal<Memory[]>([]);
  protected draft = signal('');
  protected newMemory = signal('');
  protected busy = signal(false);
  protected learned = signal(0);
  protected prefsOpen = signal(false);
  protected bg = signal<{ closeToTray: boolean; openAtLogin: boolean; supported: boolean } | null>(null);
  // Agentic task ("do it for me"): plan/questions/approve/run, inline in the conversation.
  protected task = signal<TaskState | null>(null);
  protected liveTask = computed<LiveRun | null>(() => { const id = this.task()?.runId; return id ? (this.runner.live()[id] ?? null) : null; });

  constructor() {
    void this.c.refresh();
    void this.c.messages().then(m => { this.messages.set(m); this.scroll(); }).catch(() => {});
    void this.loadMemories();
    // A suggestion accepted from a native notification (or from this page) lands here.
    effect(() => { const p = this.c.pendingPrompt(); if (p) { this.c.pendingPrompt.set(null); void this.send(p); } });
    effect(() => { const p = this.c.dockPrompt(); if (p) { this.c.dockPrompt.set(null); void this.send(p); } });   // « Déboguer avec l'IA »
    effect(() => { const b = this.c.pendingBrief(); if (b) { this.c.pendingBrief.set(null); void this.brief(b); } });
    // Accepting a "stuck" offer launches the agentic task flow (plan → questions → do).
    effect(() => { const t = this.c.pendingTask(); if (t && !this.task()) { this.c.pendingTask.set(null); void this.doTask(t); } });
    const bridge = this.desktop.companion;
    if (bridge?.getBackground) void bridge.getBackground().then(b => this.bg.set(b)).catch(() => {});
  }

  protected async setBg(patch: { closeToTray?: boolean; launchAtLogin?: boolean }) {
    const bridge = this.desktop.companion;
    if (bridge?.setBackground) this.bg.set(await bridge.setBackground(patch));
  }

  // ---- agentic tasks ("Faire") ----
  /** Start a real task from the current draft (or a given message). Plans it, then either asks
   *  clarifying questions or shows a concrete plan to approve and run. */
  protected async doTask(message?: string) {
    const q = (message ?? this.draft()).trim();
    if (!q || this.task()) return;
    this.draft.set('');
    this.task.set({ message: q, phase: 'planning', questions: [], answers: {}, plan: null });
    await this.planStep({});
  }
  private async planStep(answers: Record<string, string>) {
    const t = this.task();
    if (!t) return;
    this.task.set({ ...t, phase: 'planning', error: undefined });
    try {
      const res = await this.c.planTask(t.message, answers, t.automationId);
      if (res.status === 'questions') {
        this.taskStep.set(0);
        // the AI's recommended option is pre-selected; the user can still pick another or type their own
        this.task.update(s => s ? { ...s, phase: 'questions', questions: res.questions, answers: seedRecommended(res.questions, answers) } : s);
      } else {
        this.task.update(s => s ? { ...s, phase: 'plan', plan: res, automationId: res.automation_id, answers } : s);
      }
    } catch (e: any) {
      const msg = await errorText(e, 'Planification impossible');
      this.task.update(s => s ? { ...s, phase: s.plan ? 'plan' : 'questions', error: msg } : s);
    }
  }
  protected setTaskAnswer(q: string, v: string) { this.task.update(s => s ? { ...s, answers: { ...s.answers, [q]: v } } : s); }
  // Question UI: selectable option cards for the suggestions + an always-present
  // free-text box for anything else. Typing in the box IS the answer (deselects the options); picking
  // an option clears the box. `taskOther` holds the per-question free-text.
  private taskOther = signal<Record<string, string>>({});
  protected otherText(q: TaskQuestion): string {
    const t = this.taskOther()[q.question];
    if (t !== undefined) return t;
    const a = this.task()?.answers[q.question] ?? '';
    return a && !q.options.includes(a) ? a : '';   // a saved custom answer shows in the box
  }
  protected onOther(q: TaskQuestion, v: string) {
    this.taskOther.update(m => ({ ...m, [q.question]: v }));
    this.setTaskAnswer(q.question, v.trim() ? v : '');   // free text wins; empty → no answer yet
  }
  protected chooseOption(q: TaskQuestion, o: string) {
    this.taskOther.update(m => ({ ...m, [q.question]: '' }));   // clear the box when picking an option
    this.setTaskAnswer(q.question, o);
  }
  protected optionOn(q: TaskQuestion, o: string): boolean {
    return !(this.taskOther()[q.question] ?? '').trim() && this.task()?.answers[q.question] === o;
  }
  // One question at a time (stepper): show question N, answer, then advance to N+1; the last one plans.
  protected taskStep = signal(0);
  protected curAnswered(): boolean {
    const t = this.task();
    if (!t) return false;
    const q = t.questions[this.taskStep()];
    return !!q && (t.answers[q.question] ?? '').trim().length > 0;
  }
  protected nextQ() {
    const t = this.task();
    if (!t || !this.curAnswered()) return;
    if (this.taskStep() < t.questions.length - 1) this.taskStep.update(i => i + 1);
    else void this.planStep(t.answers);
  }
  protected prevQ() { this.taskStep.update(i => Math.max(0, i - 1)); }
  protected cancelTask() { this.task.set(null); }
  /** Debug WITH the user, right here in the chat: the companion gets the task, the error and the log. */
  protected debugTask(error: string, log: { level?: string; message: string }[] = []) {
    const t = this.task();
    const tail = log.slice(-6).map(e => `- ${e.message}`).join('\n');
    const lines = [`[DÉBOGAGE] Tâche demandée : « ${t?.message ?? ''} »`, `Erreur : « ${error} »`];
    if (tail) lines.push('Dernières lignes du journal :', tail);
    lines.push('Aide-moi à comprendre ce qui se passe et à le corriger.');
    void this.send(lines.join('\n'));
  }
  /** Approve the exact scripts the plan showed, then run it through the safe automation pipeline. */
  protected async runPlan() {
    const t = this.task();
    const plan = t?.plan;
    if (!t || !plan) return;
    this.task.update(s => s ? { ...s, error: undefined } : s);
    try {
      for (const sc of plan.scripts) await this.api.approveScript(plan.automation_id, sc.sha256);
      const a = await this.api.get(plan.automation_id);
      const { run } = await this.runner.runNow(a, false);
      this.task.update(s => s ? { ...s, phase: 'running', runId: run.id } : s);
    } catch (e: any) {
      const msg = await errorText(e, 'Lancement impossible');
      this.task.update(s => s ? { ...s, error: msg } : s);
    }
  }
  protected answerConfirm(ok: boolean) { const id = this.task()?.runId; if (id) this.runner.answerStepConfirm(id, ok); }

  protected greeting() { const h = new Date().getHours(); return h < 12 ? 'Bonjour' : h < 18 ? 'Bon après-midi' : 'Bonsoir'; }
  protected mins(s: number) { const m = Math.round(s / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`; }
  protected pct(a: number, b: number) { return b ? Math.round((100 * a) / b) : 0; }
  protected time(iso: string) { const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z'); return isNaN(+d) ? '' : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }); }
  protected catLabel(c: string) { return CATS[c] ?? c; }
  protected kindLabel(k: string) { return KINDS[k] ?? k; }

  protected async finishOnboarding() {
    await this.c.saveProfile({ job_role: this.draftRole(), tone: this.draftTone(), tracking_enabled: this.draftTracking(), onboarded: true });
  }

  protected save(patch: Record<string, unknown>) { void this.c.saveProfile(patch as any); }
  protected setWeight(kind: string, value: string) { void this.c.saveProfile({ nudge_weights: { [kind]: Number(value) } }); }

  protected async accept(o: Suggestion) { await this.c.accept(o); }

  protected async send(text: string) {
    const q = text.trim();
    if (!q || this.busy()) return;
    this.draft.set('');
    this.messages.update(m => [...m, { role: 'user', content: q }]);
    await this.run('chat', { message: q });
    if (/^\s*(souviens|retiens|remember|note|n'oublie)/i.test(q)) void this.loadMemories();
  }

  protected async brief(kind: 'morning' | 'evening') {
    if (this.busy()) return;
    await this.run('brief', { kind });
  }

  private async run(path: 'chat' | 'brief', body: object) {
    this.busy.set(true);
    this.learned.set(0);
    const reply: ChatMessage = { role: 'assistant', content: '', sources: [], streaming: true };
    this.messages.update(m => [...m, reply]);
    this.scroll();
    try {
      await this.c.stream(path, body, ev => {
        if (ev.type === 'meta') reply.sources = ev.sources ?? [];
        else if (ev.type === 'delta') reply.content += ev.text;
        else if (ev.type === 'done') { if (!reply.content) reply.content = ev.answer ?? ''; if (ev.learned) { this.learned.set(ev.learned); void this.loadMemories(); } }
        this.messages.update(m => [...m]);
        this.scroll();
      });
    } catch (e: any) {
      reply.content = reply.content || (e?.message ?? 'Une erreur est survenue.');
    } finally {
      reply.streaming = false;
      this.messages.update(m => [...m]);
      this.busy.set(false);
    }
  }

  private async loadMemories() { try { this.memories.set(await this.c.memories()); } catch { /* not allowed yet */ } }
  protected async addMemory() {
    const text = this.newMemory().trim();
    if (text.length < 4) return;
    this.newMemory.set('');
    await this.c.addMemory(text, 'fact');
    await this.loadMemories();
  }
  protected async togglePin(m: Memory) { await this.c.updateMemory(m.id, { pinned: !m.pinned }); await this.loadMemories(); }
  protected async removeMemory(m: Memory) { await this.c.deleteMemory(m.id); await this.loadMemories(); }

  protected async exportData() {
    const data = await this.c.exportMine();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'mon-compagnon.json' });
    a.click();
    URL.revokeObjectURL(url);
  }

  protected async erase(scope: 'all' | 'activity' | 'messages') {
    const label = scope === 'all' ? 'tout ce que le compagnon sait de vous (mémoires, activité, conversation, préférences)' : scope === 'activity' ? 'votre historique d’activité' : 'la conversation';
    if (!confirm(`Effacer définitivement ${label} ?`)) return;
    await this.c.eraseMine(scope);
    if (scope !== 'activity') this.messages.set([]);
    await this.loadMemories();
  }

  private scroll() { requestAnimationFrame(() => { const el = this.feed()?.nativeElement; if (el) el.scrollTop = el.scrollHeight; }); }
}
