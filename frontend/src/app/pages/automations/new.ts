import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { Desktop } from '../../desktop';
import { AutomationApi, errorText } from '../../automations/automation-api';
import { draftFromCompile, notesFromCompile, saveNotes } from '../../automations/drafts';
import { RecordingProcessor } from '../../automations/recording';

const EXAMPLES: { title: string; text: string }[] = [
  {
    title: 'Conventions de stage (RH)',
    text: 'Chaque lundi matin, un mail des RH arrive avec en pièce jointe un fichier Excel listant les nouveaux stagiaires (nom, prénom, email, date de début). '
      + 'Pour chaque stagiaire : remplir mon modèle Word de convention avec ses informations, l’imprimer, puis le lui envoyer par mail avec la convention en pièce jointe.',
  },
  {
    title: 'Rapport hebdomadaire',
    text: 'Chaque vendredi à 17h, rassembler les mails reçus cette semaine dont l’objet contient « incident », en faire un résumé en quelques puces avec l’IA et me l’envoyer par mail.',
  },
  {
    title: 'Factures fournisseurs',
    text: 'Quand je reçois un mail avec une facture PDF en pièce jointe de la part de « compta@fournisseur.fr », enregistrer la pièce jointe dans Documents\\Factures puis l’imprimer.',
  },
];

@Component({
  selector: 'app-automation-new',
  imports: [RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <a class="crumb" routerLink="/automations">← Automatisations</a>
          <h1>Nouvelle automatisation</h1>
          <p>Montrez la tâche une fois, ou décrivez-la avec vos mots. L'IA propose un enchaînement d'étapes que vous relisez avant toute exécution.</p>
        </div>
      </div>

      <div class="tabs" role="tablist">
        <a role="tab" [class.active]="tab() === 'describe'" [attr.aria-selected]="tab() === 'describe'" (click)="tab.set('describe')">Décrire</a>
        <a role="tab" [class.active]="tab() === 'record'" [attr.aria-selected]="tab() === 'record'" (click)="tab.set('record')">
          Enregistrer @if (!desktop.available) { <span class="pill tiny">app bureau</span> }
        </a>
      </div>

      @if (tab() === 'describe') {
        <div class="grid two">
          <div class="card">
            <label for="desc">Que faut-il automatiser ?</label>
            <textarea id="desc" rows="9" [value]="description()" (input)="description.set($any($event.target).value)"
              placeholder="Quand… (déclencheur), faire… (étapes), pour chaque… (boucle), puis… (résultat). Mentionnez les fichiers, mails, dossiers et personnes concernés."></textarea>
            <div class="row between">
              <span class="small faint">{{ description().trim().length }} caractères · plus c'est précis, moins l'IA aura de questions.</span>
              <button class="primary" [disabled]="description().trim().length < 15 || compiling()" (click)="compile()">
                @if (compiling()) { <span class="spin light"></span> Génération… } @else { Générer l'automatisation }
              </button>
            </div>
            @if (compiling()) { <p class="small muted progress">L'IA analyse votre description et construit les étapes — cela peut prendre jusqu'à une minute.</p> }
            @if (error()) { <p class="small err">{{ error() }}</p> }
          </div>
          <div class="examples">
            <div class="small muted head">Exemples</div>
            @for (ex of examples; track ex.title) {
              <button class="example" (click)="description.set(ex.text)">
                <strong>{{ ex.title }}</strong>
                <span class="small muted">{{ ex.text }}</span>
              </button>
            }
          </div>
        </div>
      } @else {
        @if (!desktop.available) {
          <div class="card center">
            <h2>Enregistrement disponible dans l'application bureau</h2>
            <p class="muted">Un navigateur ne peut pas observer les autres applications de votre PC. Installez l'application bureau DeliveryOps, ou décrivez la tâche avec vos mots.</p>
            <div class="row mid"><a class="btn primary" routerLink="/download">Télécharger l'app bureau</a><button (click)="tab.set('describe')">Décrire plutôt</button></div>
          </div>
        } @else if (!desktop.automation) {
          <div class="card center"><p>Mettez à jour l'application bureau DeliveryOps pour enregistrer une tâche.</p></div>
        } @else {
          @switch (rec.phase()) {
            @case ('recording') {
              <div class="card center">
                <div class="rec-dot"></div>
                <h2>Enregistrement en cours</h2>
                <p class="muted">Faites la tâche normalement. Utilisez la petite barre en haut de l'écran pour mettre en pause ou arrêter.</p>
                <p class="small faint">{{ rec.recorder().keyframes ?? 0 }} capture(s) · {{ rec.recorder().events ?? 0 }} événement(s)</p>
                <button class="ko" (click)="stopRecording()">Arrêter l'enregistrement</button>
              </div>
            }
            @case ('idle') {
              <div class="grid two">
                <div class="card">
                  <h2>Enregistrer une tâche</h2>
                  <p class="muted mt">Faites la tâche une fois comme d'habitude, en la commentant à voix haute si vous le souhaitez. L'IA s'en sert pour comprendre votre intention — elle ne rejoue pas vos clics.</p>
                  <label class="check"><input type="checkbox" [checked]="narration()" (change)="narration.set($any($event.target).checked)" /> Commenter à voix haute (micro)</label>
                  <button class="primary big" [disabled]="starting()" (click)="startRecording()">
                    @if (starting()) { <span class="spin light"></span> } Démarrer l'enregistrement
                  </button>
                  @if (error()) { <p class="small err">{{ error() }}</p> }
                  <p class="small faint">Limite : 10 minutes ou 150 captures d'écran.</p>
                </div>
                <div class="card privacy">
                  <h3>Confidentialité</h3>
                  <ul>
                    <li><strong>Votre écran est capturé</strong> (images régulières) pendant l'enregistrement et envoyé à votre serveur DeliveryOps pour analyse.</li>
                    <li><strong>Mettez en pause</strong> avant d'afficher ou de saisir une information sensible (mot de passe, données personnelles…).</li>
                    <li><strong>Les caractères tapés ne sont jamais enregistrés</strong> : seulement « a saisi N caractères » et les raccourcis (Ctrl+C, Entrée…).</li>
                    <li>Les clics, le titre de la fenêtre active et votre commentaire vocal sont enregistrés.</li>
                    @if (hookMissing()) { <li class="warn">Sur ce poste, les clics et touches ne peuvent pas être captés : seules les images et les fenêtres seront enregistrées.</li> }
                  </ul>
                </div>
              </div>
            }
            @case ('error') {
              <div class="card center">
                <h2>L'enregistrement n'a pas pu être traité</h2>
                <p class="err">{{ rec.message() }}</p>
                <div class="row mid"><button (click)="rec.reset()">Recommencer</button><button class="ghost" (click)="tab.set('describe')">Décrire plutôt</button></div>
              </div>
            }
            @default {
              <div class="card center">
                <h2>Préparation de votre automatisation</h2>
                <ol class="phases">
                  <li [class.done]="phaseIndex() > 0" [class.on]="phaseIndex() === 0">Envoi de l'enregistrement</li>
                  <li [class.done]="phaseIndex() > 1" [class.on]="phaseIndex() === 1">Analyse des captures d'écran</li>
                  <li [class.on]="phaseIndex() === 2">Génération des étapes</li>
                </ol>
                @if (rec.message()) { <p class="small faint">{{ rec.message() }}</p> }
              </div>
            }
          }
        }
      }
    </div>
  `,
  styles: `
    .crumb { display: inline-block; font-size: 12px; margin-bottom: 4px; color: var(--muted); }
    .tabs a { cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
    .pill.tiny { font-size: 10px; padding: 0 6px; }
    .two { grid-template-columns: minmax(0, 3fr) minmax(260px, 2fr); align-items: start; }
    textarea { resize: vertical; min-height: 180px; line-height: 1.5; margin-bottom: 10px; }
    .between { justify-content: space-between; }
    .progress { margin-top: 10px; }
    .err { color: var(--red); margin-top: 10px; }
    .spin.light { border-color: rgba(255,255,255,.35); border-top-color: #fff; }
    .examples { display: flex; flex-direction: column; gap: 8px; }
    .examples .head { text-transform: uppercase; letter-spacing: .06em; font-size: 11px; }
    .example { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left; font-weight: 400; padding: 10px 12px;
      transition: border-color .15s ease, transform .15s ease; }
    .example:hover { border-color: var(--accent); transform: translateY(-1px); }
    .example .muted { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .center { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 32px 24px; }
    .center p { max-width: 560px; }
    .mid { justify-content: center; }
    .mt { margin: 6px 0 14px; }
    .check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); margin-bottom: 14px; cursor: pointer; }
    .check input { width: auto; }
    .big { padding: 10px 18px; margin-bottom: 10px; }
    .privacy ul { margin: 8px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: var(--muted); }
    .privacy strong { color: var(--text); font-weight: 600; }
    .privacy .warn { color: var(--amber); }
    .rec-dot { width: 14px; height: 14px; border-radius: 50%; background: #e5484d; animation: pulse 1.4s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(229,72,77,.5); } 50% { box-shadow: 0 0 0 8px rgba(229,72,77,0); } }
    .phases { list-style: none; padding: 0; margin: 6px 0; display: flex; flex-direction: column; gap: 8px; text-align: left; }
    .phases li { color: var(--faint); padding-left: 24px; position: relative; transition: color .2s ease; }
    .phases li::before { content: ''; position: absolute; left: 0; top: 4px; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--border); }
    .phases li.on { color: var(--text); font-weight: 500; }
    .phases li.on::before { border-color: var(--accent); border-top-color: transparent; animation: spin .8s linear infinite; }
    .phases li.done { color: var(--green); }
    .phases li.done::before { background: var(--green); border-color: var(--green); }
    @media (max-width: 900px) { .two { grid-template-columns: 1fr; } }
  `,
})
export class AutomationNewPage {
  private api = inject(AutomationApi);
  private router = inject(Router);
  protected desktop = inject(Desktop);
  protected rec = inject(RecordingProcessor);
  protected examples = EXAMPLES;

  protected tab = signal<'describe' | 'record'>('describe');
  protected description = signal('');
  protected compiling = signal(false);
  protected starting = signal(false);
  protected narration = signal(true);
  protected error = signal('');
  protected hookMissing = signal(false);
  protected phaseIndex = computed(() => ({ uploading: 0, analyzing: 1, compiling: 2 } as Record<string, number>)[this.rec.phase()] ?? 0);

  constructor() {
    const q = inject(ActivatedRoute).snapshot.queryParamMap.get('tab');
    if (q === 'record' || q === 'describe') this.tab.set(q);
    else if (this.rec.phase() !== 'idle') this.tab.set('record');
    this.desktop.automation?.capabilities().then(c => this.hookMissing.set(!c.hook)).catch(() => {});
  }

  protected async compile() {
    const text = this.description().trim();
    this.compiling.set(true);
    this.error.set('');
    try {
      const compiled = await this.api.compile({ description: text });
      const created = await this.api.create(draftFromCompile(compiled, { kind: 'prompt', prompt: text }));
      saveNotes(created.id, notesFromCompile(compiled));
      await this.router.navigateByUrl(`/automations/${created.id}`);
    } catch (err: any) {
      this.error.set(err?.status === 404 ? 'Le serveur ne propose pas encore les automatisations (mise à jour du backend nécessaire).' : `Génération impossible : ${errorText(err)}`);
    } finally {
      this.compiling.set(false);
    }
  }

  protected async startRecording() {
    this.starting.set(true);
    this.error.set('');
    try { await this.rec.start(this.narration()); }
    catch (err) { this.error.set(errorText(err)); }
    finally { this.starting.set(false); }
  }

  protected stopRecording() { void this.desktop.automation?.stopRecording(); }
}
