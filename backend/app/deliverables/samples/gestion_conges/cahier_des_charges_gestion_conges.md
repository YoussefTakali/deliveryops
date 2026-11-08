# Cahier des charges — Application de gestion des congés (DRH Groupe Nova)

## 1. Contexte
La DRH du groupe Nova gère aujourd'hui les demandes de congés par email et tableur, source d'erreurs
de solde et de retards de validation. Elle souhaite une application web interne.

## 2. Objectifs
- Supprimer la saisie manuelle des soldes.
- Valider une demande en moins de 48 h.
- Donner aux managers une vision d'équipe.

## 3. Exigences

- EX-01 : Un collaborateur peut déposer une demande de congé (dates, type, commentaire).
- EX-02 : Le manager reçoit la demande et peut l'accepter ou la refuser avec un motif.
- EX-03 : Le solde de congés est calculé automatiquement et affiché en temps réel.
- EX-04 : Un calendrier d'équipe affiche les absences validées.
- EX-05 : Les jours fériés et fermetures d'entreprise sont pris en compte par pays.
- EX-06 : Export mensuel des absences vers le logiciel de paie (fichier CSV).
- EX-07 : Notification par email à chaque changement d'état d'une demande.
- EX-08 : Historique et annulation d'une demande tant qu'elle n'est pas commencée.
- EX-09 : Authentification via l'annuaire d'entreprise (SSO).

## 4. Contraintes
- Application accessible depuis l'intranet uniquement.
- Données hébergées en Europe.
