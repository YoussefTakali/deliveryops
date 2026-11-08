# Spécification fonctionnelle — Sinistres, paiement et notifications

Projet : Portail Client ACME Assurances · Version 1.0 · Statut : en revue

## US-07 — Déclarer un sinistre
En tant qu'assuré, je veux déclarer un sinistre en ligne en joignant des photos et le constat afin
d'ouvrir un dossier sans appeler mon conseiller.
- Exigences couvertes : EXF-04
- Règles : 10 pièces jointes max, 10 Mo chacune (jpg, png, pdf) ; brouillon enregistré automatiquement.
- Critères d'acceptation : numéro de dossier affiché immédiatement ; accusé de réception par email.

## US-08 — Suivre mon sinistre
En tant qu'assuré, je veux consulter l'état d'avancement de mon dossier sinistre afin de savoir où en
est mon indemnisation.
- Exigences couvertes : EXF-05, EXF-09
- Critères d'acceptation : étapes « reçu / en expertise / accord / indemnisé » ; date de dernière mise
  à jour.

## US-09 — Écrire à mon conseiller
En tant qu'assuré, je veux échanger avec mon conseiller via une messagerie sécurisée afin de poser
une question sur mon contrat.
- Exigences couvertes : EXF-06, EXNF-03
- Règles : messages horodatés, pièces jointes autorisées, accusé de lecture.

## US-10 — Payer une cotisation en ligne
En tant qu'assuré, je veux régler une cotisation par carte bancaire afin de régulariser ma situation.
- Exigences couvertes : EXF-08, EXNF-03
- Règles : paiement via le PSP du groupe (3‑D Secure) ; aucune donnée carte stockée côté portail.
- Critères d'acceptation : reçu PDF ; statut du contrat mis à jour sous 2 minutes.

## US-11 — Exporter mes paiements
En tant qu'assuré, je veux exporter l'historique de mes paiements au format CSV afin de le transmettre
à mon comptable.
- Exigences couvertes : EXF-10

## US-12 — Recevoir des notifications
En tant qu'assuré, je veux recevoir un email ou une notification push lors d'un événement sur mon
contrat afin d'être informé sans me connecter.
- Exigences couvertes : EXF-09
- Règles : préférences de notification modifiables ; désabonnement en un clic.
