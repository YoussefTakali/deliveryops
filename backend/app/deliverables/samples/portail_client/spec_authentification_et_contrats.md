# Spécification fonctionnelle — Authentification et espace contrats

Projet : Portail Client ACME Assurances · Version 1.2 · Statut : validée

## US-01 — Connexion de l'assuré
En tant qu'assuré, je veux me connecter avec mon identifiant et mon mot de passe afin d'accéder à
mon espace personnel.
- Exigences couvertes : EXF-01
- Règles : 5 tentatives max puis blocage 15 min ; mot de passe 12 caractères minimum.
- Critères d'acceptation : message d'erreur générique en cas d'échec ; journalisation de la tentative.

## US-02 — Double authentification par SMS
En tant qu'assuré, je veux recevoir un code par SMS lors de la connexion depuis un nouvel appareil
afin de sécuriser mon compte.
- Exigences couvertes : EXF-01, EXNF-03
- Règles : code à 6 chiffres valable 5 minutes ; 3 saisies max.

## US-03 — Consultation de mes contrats
En tant qu'assuré, je veux voir la liste de mes contrats avec leurs garanties principales afin de
connaître ma couverture.
- Exigences couvertes : EXF-02
- Critères d'acceptation : affichage du numéro, du type (auto/habitation/santé), de la date d'échéance
  et des garanties ; tri par date.

## US-04 — Téléchargement d'une attestation
En tant qu'assuré, je veux télécharger mon attestation d'assurance en PDF afin de la fournir à un
tiers.
- Exigences couvertes : EXF-03
- Règles : PDF généré à la demande, filigrané, horodaté.

## US-05 — Mise à jour de mes coordonnées
En tant qu'assuré, je veux modifier mon adresse, mon téléphone et mon email afin que mes informations
soient à jour.
- Exigences couvertes : EXF-07
- Règles : confirmation par email de tout changement d'email ; adresse validée via le référentiel
  postal.

## US-06 — Reprise des comptes existants
En tant qu'assuré déjà inscrit sur l'ancien portail, je veux retrouver mon compte sans ressaisie afin
de ne pas recréer mes accès.
- Exigences couvertes : EXF-01
- Note : migration des comptes réalisée en lot avant l'ouverture.
