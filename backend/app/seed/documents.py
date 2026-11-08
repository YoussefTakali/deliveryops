"""Sample transfer documentation (fictional)."""

ATLAS_TRANSFER = """# Dossier de transfert — Périmètre SI Sinistres (Atlas Assurances)

# Contexte
Le périmètre SI Sinistres couvre la déclaration, l'instruction et l'indemnisation des sinistres IARD
pour environ 1 200 gestionnaires et 3 500 experts partenaires. Le prestataire sortant assure la TMA
depuis 2019 ; la réversibilité doit être achevée en 6 semaines.

# Applications du périmètre
ClaimsPortal est le portail de déclaration des sinistres (Angular 12, Java Spring Boot). Il est exposé aux assurés.
SinistreCore est le cœur de gestion (Java EE 7, WebLogic 12, Oracle 12c). Toutes les applications du périmètre en dépendent.
BatchIndemnisation calcule et émet les paiements chaque nuit (COBOL, ordonnancé par Control-M).
GED Documents stocke les pièces justificatives (Alfresco 5.2).
ReferentielClients est le référentiel des assurés partagé avec d'autres SI.
EditiqueDocs produit les courriers (OpenText Exstream).
PortailExperts permet aux experts de déposer leurs rapports (PHP 5.6).
ReportingSinistres fournit les tableaux de bord métier (Business Objects).

# Points d'attention
Le batch BatchIndemnisation échoue régulièrement en cas de volumétrie élevée ; la reprise est manuelle et
n'est documentée que partiellement. Seule une personne de l'équipe sortante maîtrise le code COBOL.
PortailExperts repose sur PHP 5.6, version en fin de support depuis 2019, avec des vulnérabilités connues non corrigées.
Alfresco 5.2 est obsolète : la migration vers une version supportée n'est pas planifiée.
Les mots de passe des comptes techniques Oracle sont stockés dans des scripts shell sur le serveur batch.
Le certificat TLS de ClaimsPortal est renouvelé manuellement chaque année ; un oubli a provoqué une indisponibilité en 2023.

# Organisation actuelle
Astreinte assurée du lundi au samedi, 7h-21h. Les incidents P1 sont escaladés au Responsable d'exploitation client.
Comité de suivi mensuel, COPIL trimestriel.
"""

ATLAS_DAT = """# Dossier d'Architecture Technique — SI Sinistres

# Vue d'ensemble
ClaimsPortal appelle SinistreCore via des API REST exposées derrière un reverse proxy Apache.
ClaimsPortal appelle GED Documents pour le dépôt des pièces jointes (API CMIS).
SinistreCore interroge ReferentielClients en temps réel (SOAP) pour vérifier les contrats.
BatchIndemnisation lit directement les tables de SinistreCore (base Oracle partagée) et produit un fichier SEPA.
EditiqueDocs reçoit les demandes de courriers depuis SinistreCore via une file MQ (IBM MQ).
PortailExperts pousse les rapports d'expertise vers SinistreCore via API REST.
ReportingSinistres extrait les données de SinistreCore chaque nuit (ETL).

# Infrastructure
Serveurs WebLogic en cluster actif/passif. Base Oracle 12c non répliquée : pas de plan de reprise d'activité testé depuis 2021.
Le serveur batch est unique, sans redondance.

# Sécurité
Authentification des gestionnaires via SSO SAML. PortailExperts utilise une authentification locale (login / mot de passe).
"""

ATLAS_DEX = """# Dossier d'Exploitation — SI Sinistres

# Chaîne batch nocturne
22h00 : extraction ReportingSinistres. 23h30 : BatchIndemnisation (durée nominale 2h).
En cas d'échec de BatchIndemnisation : vérifier l'espace disque du tablespace INDEMN, purger les fichiers temporaires,
relancer l'étape IND040 depuis Control-M. Si l'échec survient après l'étape IND060, la reprise est manuelle
et nécessite l'intervention d'un développeur COBOL (procédure non documentée).
Le fichier SEPA doit être déposé avant 6h00 pour un paiement à J.

# Supervision
Sondes Centreon sur ClaimsPortal et SinistreCore. Aucune supervision fonctionnelle sur EditiqueDocs.

# Procédures courantes
Purge des logs WebLogic chaque dimanche. Renouvellement du certificat TLS ClaimsPortal : procédure manuelle annuelle.
Redémarrage de GED Documents en cas de saturation mémoire (fréquent en fin de mois).
"""

ATLAS_CONTRACT = """# Contrat de Tierce Maintenance Applicative — Atlas Assurances

# Article 4 — Périmètre
Le prestataire assure la maintenance corrective, la maintenance préventive, le maintien en conditions opérationnelles
et les évolutions mineures des applications listées en annexe 1.
Sont exclus du périmètre : la migration de données, les nouvelles applications, la formation utilisateurs,
le support infrastructure réseau et la refonte d'application.

# Article 7 — Conditions financières
La maintenance corrective et le MCO sont facturés au forfait mensuel de 18 000 € HT.
Les évolutions sont facturées en unités d'œuvre selon le catalogue : UO-S petite évolution 1 200 € (14 h maximum),
UO-M évolution moyenne 3 400 € (40 h maximum), UO-L évolution majeure 7 800 € (90 h maximum).
Les prestations en régie sont facturées au tarif journalier : junior 420 €, confirmé 560 €, senior 680 €, architecte 850 €,
sur la base de 7 heures par jour, dans la limite d'un plafond mensuel de 12 000 € HT sauf accord écrit du client.

# Article 9 — Niveaux de service
Délais de résolution : P1 4 heures, P2 8 heures, P3 40 heures, P4 80 heures.
Taux de respect attendu : 95 % pour P1 et P2, 90 % pour P3 et P4.

# Article 10 — Pénalités
Chaque point de pourcentage sous l'objectif donne lieu à une pénalité de 1 % du forfait mensuel, plafonnée à 10 %.
"""

HORIZON_TRANSFER = """# Dossier de transfert — Paiements & Monétique (Banque Horizon)

# Applications
PayHub orchestre les virements SEPA et instantanés (Java 17, Kafka).
CardManager gère le cycle de vie des cartes bancaires (Java, PostgreSQL).
FraudShield score les transactions en temps réel (Python, modèle de machine learning).
ClearingBatch réalise la compensation quotidienne (Spring Batch).
MerchantPortal est le portail des commerçants (React).

# Points d'attention
FraudShield dépend d'un modèle réentraîné manuellement chaque trimestre par un data scientist du client ; aucun runbook.
La montée de version Kafka 2.8 vers 3.x est à planifier : la version actuelle arrive en fin de support.
"""

HORIZON_CONTRACT = """# Contrat TMA — Banque Horizon
# Périmètre
Maintenance corrective et évolutive. Exclus : migration de données, nouvelles applications, formation utilisateurs.
# Conditions financières
Forfait mensuel 14 500 € HT. UO-S 1 300 € (14 h), UO-M 3 600 € (40 h). Régie : confirmé 590 €, senior 720 €, plafond 10 000 €.
# Niveaux de service
P1 2 heures, P2 6 heures, P3 32 heures, P4 72 heures. Objectifs 98 % P1/P2, 92 % P3/P4.
"""

DEMO_MEETING = """Directeur SI client: Bonjour à tous, on commence par le point sur les incidents du mois.
Delivery Manager: Nous avons eu 3 échecs du batch d'indemnisation ce mois-ci, tous repris avant 6h sauf un.
Directeur SI client: C'est justement notre inquiétude, un retard de paiement nous expose vis-à-vis des assurés.
Responsable métier: Pourquoi le batch indemnisation échoue-t-il aussi souvent en fin de mois ?
Delivery Manager: Je m'en charge : on vous envoie une analyse de cause racine d'ici vendredi.
Responsable métier: Par ailleurs nous souhaitons une migration des données historiques de la GED vers le nouveau stockage objet.
Directeur SI client: Et il faudrait prévoir une formation utilisateurs pour les nouveaux experts en mars.
Delivery Manager: On revient vers vous avec un chiffrage pour ces deux sujets.
Directeur SI client: On valide le passage en production de la nouvelle version du portail experts le 15.
Architecte: Attention, PortailExperts tourne encore en PHP 5.6, c'est un risque sécurité bloquant à moyen terme.
"""
