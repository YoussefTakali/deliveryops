# Cahier des charges — Portail Client ACME Assurances

## 1. Contexte
ACME Assurances souhaite refondre son portail client afin de permettre aux assurés de consulter
leurs contrats, déclarer un sinistre et échanger avec leur conseiller en ligne. Le portail actuel,
développé en 2014, ne répond plus aux exigences de sécurité ni aux attentes d'usage mobile.

## 2. Objectifs
- Réduire de 30 % les appels au centre de relation client sur les demandes simples.
- Offrir une expérience mobile complète (responsive) avant le 31 mars 2027.
- Se conformer au RGPD et aux recommandations de l'ANSSI pour l'authentification.

## 3. Périmètre
Assurés particuliers (auto, habitation, santé). Hors périmètre : professionnels et courtiers.

## 4. Exigences fonctionnelles

| Réf. | Exigence | Impact | Probabilité d'apparition |
|------|----------|--------|--------------------------|
| EXF-01 | Authentification de l'assuré par identifiant + mot de passe, avec double authentification par SMS | Catastrophique | Inévitable |
| EXF-02 | Consultation de la liste des contrats et de leurs garanties | Grave | Inévitable |
| EXF-03 | Téléchargement des attestations et des conditions particulières au format PDF | Grave | Fréquent |
| EXF-04 | Déclaration d'un sinistre en ligne avec pièces jointes (photos, constat) | Catastrophique | Fréquent |
| EXF-05 | Suivi de l'avancement d'un sinistre déclaré | Grave | Fréquent |
| EXF-06 | Messagerie sécurisée avec le conseiller | Modéré | Occasionnel |
| EXF-07 | Modification des coordonnées (adresse, téléphone, email) | Modéré | Fréquent |
| EXF-08 | Paiement en ligne d'une cotisation par carte bancaire | Catastrophique | Occasionnel |
| EXF-09 | Réception de notifications (email / push) sur les événements du contrat | Ennuyeux | Fréquent |
| EXF-10 | Export de l'historique des paiements (CSV) | Ennuyeux | Rare |

## 5. Exigences non fonctionnelles

| Réf. | Exigence | Impact | Probabilité d'apparition |
|------|----------|--------|--------------------------|
| EXNF-01 | Disponibilité du portail 99,5 % (hors maintenance planifiée) | Grave | Occasionnel |
| EXNF-02 | Temps de réponse inférieur à 2 secondes pour 95 % des pages | Modéré | Fréquent |
| EXNF-03 | Chiffrement des données en transit (TLS 1.2+) et au repos | Catastrophique | Rare |
| EXNF-04 | Accessibilité RGAA niveau AA | Modéré | Occasionnel |

## 6. Contraintes
- Intégration avec le SI de gestion des contrats (API REST existante).
- Hébergement sur le cloud souverain retenu par le groupe.
- Reprise des comptes existants sans ressaisie par les assurés.

## 7. Livrables attendus du prestataire
- Spécifications détaillées validées.
- Matrice de couverture des exigences par les cas de test.
- Recette fonctionnelle et rapport de tests.
