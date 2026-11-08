# Livrable : Matrice de couverture (de test)

Référence méthode maison (source : deck "Préparer matrice de couverture" fourni). Sert de base à la
génération ; chaque société peut surcharger les échelles et intitulés (voir per-company overrides).

## Objet
Construire une matrice qui croise les **exigences métier** avec les **cas d'usage / user stories**,
évalue la **criticité** de chaque exigence, puis en déduit l'**effort / la charge de test**.

## Méthode en 4 étapes
1. **Créer la matrice** : croiser les exigences métier (colonnes X1…Xn) avec les cas d'usage / user
   stories (lignes A01…An). Chaque cellule marque si un cas d'usage couvre l'exigence.
2. **Identifier les dépendances** entre exigences.
3. **Matrice carrée de régression** : à partir du nombre de dépendances de l'étape 2, calculer la
   sensibilité et l'influence de chaque exigence → identifie l'effort de test à produire (modélisation
   des dépendances fonctionnelles).
4. **Charge de test** : calculer la charge, puis intégrer la structure dans un outil de gestion de test.

## Analyse de criticité (par exigence)
Chaque exigence porte : **Impact** (gravité) et **Probabilité d'apparition**. La **Criticité** est lue
dans la grille de risque Impact × Probabilité :

| Impact \ Probabilité | Inévitable | Fréquent | Occasionnel | Rare |
|----------------------|-----------|----------|-------------|------|
| Catastrophique       | 3         | 3        | 3           | 2    |
| Grave                | 3         | 3        | 2           | 1    |
| Modéré               | 2         | 2        | 1           | 1    |
| Ennuyeux             | 2         | 1        | 1           | 1    |

Échelles (par défaut, surchargeables par société) :
- Impact : Catastrophique, Grave, Modéré, Ennuyeux
- Probabilité : Inévitable, Fréquent, Occasionnel, Rare
- Criticité : 1 (faible) … 3 (forte)

## Questions à poser (une à la fois) pour générer une matrice
- Nom du projet / périmètre ? Client / société ?
- Liste des exigences métier (avec, pour chacune, Impact et Probabilité si connus) ?
- Liste des cas d'usage / user stories ?
- Couverture connue (quel cas d'usage couvre quelle exigence) ? (sinon on laisse à remplir)
- Dépendances entre exigences (si connues) ?

## Sortie
Classeur (tableur) : feuille « Matrice » (exigences × cas d'usage, avec Impact/Probabilité/Criticité
par exigence), feuille « Criticité » (la grille ci-dessus), et — si dépendances fournies — feuille
« Régression » (matrice carrée) + « Charge de test ».
