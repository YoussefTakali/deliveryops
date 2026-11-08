# DeliveryOps — script de la démonstration (cas réel)

Durée de la vidéo : **13:55**. Une ligne par scène : minutage, ce qui se passe à l’écran, texte à dire.

## 0:00 – 0:29 · Introduction
*À l’écran :* Carte titre

DeliveryOps en situation réelle. Nous reprenons un contrat de maintenance applicative chez un nouveau client, la Mutuelle Vitalis Santé, avec les vrais documents d'une reprise : le contrat, le dossier de transfert, les exports Jira, les temps saisis, le cahier des charges et une transcription de réunion Teams. Tout est exécuté en direct par l'application ; seules les attentes de l'IA sont accélérées, et c'est indiqué à l'écran.

> Note : Dire clairement : client fictif, documents construits pour la démo mais réalistes, tout est exécuté en direct.

## 0:29 – 0:47 · Le portefeuille
*À l’écran :* Portefeuille delivery : deux clients existants

Le portefeuille du directeur delivery : deux clients existants, chacun avec la santé de son contrat calculée depuis ses tickets. Le contrat Vitalis démarre le premier octobre. Nous allons créer le client, son périmètre et son projet, puis charger les données réelles.

## 0:47 – 1:09 · Nouveau client et périmètre
*À l’écran :* Écran Nouveau périmètre → création → paramétrage

Écran Nouveau périmètre : je crée le client Mutuelle Vitalis Santé et son périmètre SI Adhésions et Remboursements, avec une description libre. Quelques secondes, et l'application m'emmène directement vers le paramétrage.

## 1:09 – 1:31 · Le projet
*À l’écran :* Création du projet TMA Vitalis Santé → rattachement du périmètre

Un périmètre appartient à un projet : c'est le projet qui porte l'équipe, les livrables et le plan de charge. Je crée le projet TMA Vitalis Santé avec ses technologies, qui serviront à classer les personnes par compétence, puis je rattache le périmètre depuis son paramétrage.

## 1:31 – 2:00 · Import des données réelles
*À l’écran :* 6 fichiers CSV importés : applications, flux, équipe, tickets Jira, temps, backlog

Le paramétrage montre ce qu'il manque aux agents, avec un score de préparation. J'importe les exports du client, tels quels : neuf applications et leurs onze flux, l'équipe de six personnes avec compétences, capacités et absences, cent quarante et un tickets Jira de juin à septembre, cinq cent vingt-quatre lignes de temps passés et le backlog. Chaque fichier est contrôlé ligne par ligne, et le score monte.

> Note : Si on demande : les imports acceptent CSV ou Excel, les lignes en erreur sont listées, et Jira / ServiceNow peuvent être branchés en connecteur.

## 2:00 – 2:31 · Documents et extraction du contrat
*À l’écran :* Dépôt de 4 documents Word → « Extraire les règles »

Onglet Connaissance : je dépose le dossier de transfert, le dossier d'architecture, le dossier d'exploitation et le contrat, en Word. Chaque document est découpé et indexé pour la recherche et pour les agents. Puis je demande l'extraction des règles contractuelles : l'IA lit le contrat et en tire le forfait, la grille tarifaire, les unités d'œuvre, les SLA et les pénalités, en citant les clauses. Rien n'est appliqué sans validation.

## 2:31 – 2:51 · Validation des règles contractuelles
*À l’écran :* File de validation → clic « Valider »

Dans la file de validation, la proposition de l'agent Facturation liste les clauses lues dans le contrat, avec le document en source. Je valide : ces règles deviennent le contrat du périmètre, et c'est sur elles que le moteur de pré-facturation et les indicateurs vont travailler.

> Note : Point clé : l'IA propose, le code applique après validation. Les montants viennent ensuite du moteur de règles, jamais du modèle.

## 2:51 – 3:24 · Analyse d'onboarding
*À l’écran :* 3 agents en chaîne → aperçu du dossier d’onboarding (.docx)

Lancement de l'analyse d'onboarding : trois agents en chaîne. L'agent Transfert de connaissance cartographie les dépendances, identifie les applications critiques et les risques, sourcés dans la documentation ; l'agent Incidents repère les incidents récurrents ; l'agent Capacité projette la charge. Le dossier d'onboarding Word est généré à partir de ce travail : le voici, avec son contenu réel.

## 3:24 – 3:53 · Pilotage du périmètre
*À l’écran :* Indicateurs calculés, cycle de vie

Le pilotage du périmètre. Les indicateurs ne sont pas déclarés : ils sont calculés depuis les tickets importés et le contrat validé. En août, le SLA P2 n'est pas tenu, le taux de réouverture et les échecs de mise en production dépassent les cibles : le service est sous tension. Le cycle de vie, en dessous, montre ce qui reste à faire pour ce périmètre.

## 3:53 – 4:26 · Équipe
*À l’écran :* Équipe du projet (compte / saisie manuelle) → affectation au périmètre

L'équipe. Les six personnes importées sont déjà affectées au périmètre avec leur profil, leur capacité et leurs compétences. Côté projet, j'ajoute un collègue qui a un compte, et une consultante externe saisie à la main : elle sera planifiée et chiffrée comme les autres. De retour sur le périmètre, l'équipe du projet est classée selon les technologies, et je l'affecte en un clic.

## 4:26 – 5:12 · Matrice de couverture
*À l’écran :* Cahier des charges + 2 spécifications réels → questions → aperçu du .xlsx

Premier livrable : la matrice de couverture. J'utilise le vrai cahier des charges de l'espace adhérent et les deux spécifications du client. L'IA extrait les seize exigences et les vingt-quatre cas d'usage, ne pose une question que si une information manque, avec une réponse recommandée, puis génère le classeur Excel, enregistré sur le projet et indexé pour les agents.

## 5:12 – 5:49 · Plan de charge
*À l’écran :* Questions recommandées → charge répartie → aperçu du .xlsx

Le plan de charge du projet agrège la matrice et l'équipe. L'IA ne demande que ce qu'elle ne peut pas déduire : base de chiffrage, date de début, horizon, toujours avec une réponse recommandée. Chaque exigence est confiée à une personne compétente, la moins chargée, et chiffrée au tarif de son profil, celui du contrat validé. Le fichier est généré et indexé.

## 5:49 – 6:22 · Réunion : transcription Teams importée
*À l’écran :* Import .vtt → compte-rendu → aperçu du .docx

Après le comité de suivi de septembre, j'importe la transcription Teams telle quelle. L'agent Réunions rédige le compte-rendu : synthèse, décisions, actions avec responsables et échéances, risques, et nouvelles demandes du client, dont la migration de la GED, hors périmètre contractuel. Chaque action devient une proposition à valider, et le compte-rendu Word est prêt. En réunion, le même agent écoute en direct et signale les demandes hors périmètre.

## 6:22 – 6:43 · Agent Estimation
*À l’écran :* Chiffrage de la demande « paiement en trois fois »

Le client demande le paiement de la cotisation en trois fois. L'agent Estimation chiffre la demande à partir des heures réellement passées sur les évolutions similaires, propose une unité d'œuvre du contrat, et signalerait tout terme hors périmètre.

## 6:43 – 7:10 · Pré-facturation
*À l’écran :* Moteur de règles, anomalies → aperçu du .xlsx

La pré-facture d'août est calculée par le moteur de règles du contrat validé : forfait, régie au tarif de chaque profil, unités d'œuvre livrées. Et il relève les anomalies qu'un contrôleur de gestion cherche à la main : régie sans ticket, temps saisis sur une unité d'œuvre déjà facturée, journée de douze heures, jours sans saisie. L'export Excel contient les justificatifs.

> Note : Les anomalies sont détectées par des règles (pas par l'IA) : régie sans ticket, double facturation UO, journée > 10 h, jours sans saisie.

## 7:10 – 7:24 · Capacité
*À l’écran :* Prévision sur 8 semaines

La capacité projette la charge sur huit semaines à partir des temps passés, des absences déclarées et du backlog importé : les semaines en surcharge apparaissent, et l'agent propose des affectations, soumises à validation.

## 7:24 – 8:05 · Workflow COPIL
*À l’écran :* 4 agents en chaîne → validation → aperçu du .pptx

Le COPIL du quinze octobre. Le workflow enchaîne quatre agents : Incidents, Capacité, Facturation, puis Gouvernance, qui lit leurs synthèses et les décisions en attente. Le pack COPIL s'arrête à ma validation, et le support PowerPoint est généré avec les chiffres réels du périmètre : le voici.

## 8:05 – 8:32 · Question libre aux agents
*À l’écran :* Orchestrateur : Facturation → Risques → réponse sourcée

Une question libre : pourquoi le SLA P2 n'est-il pas tenu, et quel impact sur la facturation ? L'orchestrateur choisit les agents utiles, Facturation puis Risques, et la réponse croise leurs analyses avec la documentation du périmètre, sources à l'appui. En mode question, rien n'est ajouté à la file de validation.

## 8:32 – 8:56 · Assistant du périmètre
*À l’écran :* Question → outils → réponse sourcée

L'assistant du périmètre est un agent conversationnel : il choisit lui-même ses outils, indicateurs, tickets, capacité, plan de charge, connaissance, et cite ses sources. Je lui demande quels incidents P1 et P2 sont en cours, et qui dans l'équipe peut les prendre.

## 8:56 – 9:36 · Recherche et espace personnel
*À l’écran :* Recherche documentaire → Mon espace → Fichiers PC → Microsoft 365

La recherche dans la connaissance est hybride, sémantique et par mots-clés : je cherche la procédure de reprise du flux NOEMIE, et elle retrouve les étapes exactes du dossier d'exploitation, avec leur source. Chaque collaborateur a aussi son espace personnel : ses fichiers du PC, indexés par l'application bureau, et, une fois connecté, ses mails Outlook, son agenda, OneDrive, SharePoint, Teams et ses tâches. Tout reste privé ; c'est ce que le Compagnon utilise pour répondre. Dans cette démo, aucun compte Microsoft n'est connecté.

## 9:36 – 9:54 · Le Compagnon
*À l’écran :* Question sur le plan de charge

Le Compagnon est l'assistant personnel de chaque collaborateur. Il lit le même cerveau partagé : je lui demande où en est le plan de charge Vitalis, et il répond à partir du plan que nous venons de générer.

## 9:54 – 10:28 · Compagnon proactif
*À l’écran :* Blocage détecté → questions → plan avec confirmations

Il ne fait pas que répondre. Avec l'accord de la personne, l'application bureau lui transmet l'application et le titre de la fenêtre active, jamais les frappes ni le contenu. Ici, il voit plusieurs installations de Python qui échouent et propose son aide. J'accepte : il pose ses questions, chacune avec une réponse recommandée, puis prépare un plan. Chaque étape sensible demande une confirmation et les commandes exactes sont affichées avant exécution, dans l'application bureau.

## 10:28 – 11:09 · Assistant d'appel
*À l’écran :* Fenêtre bureau → question → réponse sourcée

L'assistant d'appel est une petite fenêtre de l'application bureau, posée à côté de Teams. Le client appelle : Marc Delorme signale un nouveau rejet du flux NOEMIE, demande si la migration de la GED peut entrer dans le forfait, puis le chiffrage du paiement en trois fois. L'assistant transcrit l'appel en local, distingue les voix, et propose une réponse dès que l'interlocuteur finit sa phrase : la procédure de reprise du dossier d'exploitation, la clause hors périmètre du contrat, le chiffrage à venir. Voix de synthèse pour la démo ; transcription et réponses réelles.

## 11:09 – 12:27 · Livrable propre au projet
*À l’écran :* Catalogue → description + exemple → structure → génération depuis .pptx + .csv → aperçu du .docx

Chaque entreprise a son catalogue de livrables, et chaque projet crée les siens. Je décris une note de mise en production pour le client, en joignant une note précédente comme exemple de style :
l'IA propose la structure et les entrées. Je l'enregistre, puis je la génère à partir de la vraie note de version PowerPoint et de la liste des tickets livrés.
Le document Word est produit et indexé, comme les autres.

> Note : C'est l'argument « pérennité » : chaque entreprise décrit ses propres livrables, sans développement.

## 12:27 – 12:53 · Automatisations
*À l’écran :* Tâche décrite en français → compilée en étapes

Les automatisations. Je décris une tâche répétitive en français : l'IA la compile en étapes, à rejouer pour chaque ligne d'un fichier, avec une confirmation avant chaque étape sensible.
On peut aussi l'enregistrer une fois avec l'application bureau, et la rejouer ensuite.

## 12:53 – 13:34 · Administration
*À l’écran :* Validations, audit, observabilité, gouvernance

Enfin, l'administration. La file de validation centralise tout ce que les agents proposent, avec leurs sources. Le journal d'audit trace chaque exécution, chaque décision et chaque import,
avec recherche et export. L'observabilité montre les appels, les tokens, le coût, la latence et le taux d'acceptation par agent.
Et la gouvernance fixe les quotas de tokens par jour, bloque l'IA ou un utilisateur, et plafonne chaque agent : un coût maîtrisé.

## 13:34 – 13:55 · Conclusion
*À l’écran :* Carte finale

En une séance : un périmètre opérationnel à partir des documents et des données réels du client, des livrables produits et non décrits,
des agents connectés dont chaque résultat nourrit le suivant, et un humain qui valide ce qui engage. DeliveryOps est prêt pour un pilote. Merci.

## Questions probables

**Ce sont de vrais agents, ou des prompts ?**  
Neuf agents orchestrés ont chacun leur code et leurs données (Facturation, Risques, Incidents, Capacité, Gouvernance, Estimation, Transfert de connaissance, Réunions, Documents). L'orchestrateur les ordonne selon leurs dépendances et s'arrête à la validation humaine. Trois assistants conversationnels (assistant du périmètre, Compagnon, assistant d'appel) utilisent des outils sur les mêmes données.

**Que se passe-t-il si le modèle d'IA est indisponible ?**  
Chaque agent a un repli déterministe : les chiffres (facturation, SLA, capacité) viennent toujours du moteur de règles, jamais du modèle.

**Où vont les données du client ?**  
Déploiement dans son cloud ou sur site, avec le modèle compatible OpenAI de son choix (y compris en local). La gouvernance permet de couper l'IA pour toute l'organisation ou pour un utilisateur.

**Combien coûte l'IA ?**  
Chaque appel est mesuré (tokens, latence, coût) par agent et par utilisateur ; des quotas journaliers et des plafonds par agent bornent la dépense.

**Est-ce testé chez un client ?**  
Pas encore en production : prêt pour un pilote sur un contrat réel. L'API compte plus de 200 points d'entrée et 160 tests automatisés.
