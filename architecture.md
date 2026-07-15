# Architecture logicielle de `pt_api`

Ce document donne un survol global du logiciel. Les structures binaires, offsets, flags et messages d'erreur appartiennent à [`pt_format_specs.md`](pt_format_specs.md), qui est la spécification technique normative. La surface publique et ses limitations sont résumées dans [`README.md`](README.md).

## Objectif et périmètre

`pt_api` est une bibliothèque Python autonome qui lit et modifie des sessions Pro Tools `.ptx` sans Pro Tools, SDK ou service externe. Elle travaille sur des sessions existantes, conserve les données binaires qu'elle ne comprend pas et refuse les dispositions ambiguës plutôt que de les réécrire par supposition.

- Langage : Python 3.8 ou plus récent.
- Dépendances d'exécution : bibliothèque standard uniquement.
- Distribution : module unique `pt_api.py`, empaqueté par `pyproject.toml`.
- Interface : API Python; le petit point d'entrée CLI sert seulement au test load/save.

## Organisation du dépôt

| Élément | Responsabilité |
|---|---|
| `pt_api.py` | Chiffrement, parsing, modèle mémoire, lecture, mutations et sauvegarde. |
| `tests/` | Tests unitaires des formats validés, des opérations et des chemins d'erreur. |
| `README.md` | Contrat public : fonctionnalités, signatures et limitations. |
| `pt_format_specs.md` | Bible technique : structures PTX exactes et catalogue d'erreurs. |
| `architecture.md` | Vue d'ensemble des composants et de leurs interactions. |
| `changelog.md` | Historique des changements publiés et non publiés. |
| `handoff.md` | Guide de reprise versionné pour la prochaine révision. |

## Flux principal

```text
Fichier PTX chiffré
    → validation de l'enveloppe et déchiffrement XOR
    → parsing en arbre PTBlock
    → validation structurelle de la session
    → lectures ou mutations via ProToolsSession
    → sérialisation et relocalisation des pointeurs
    → rechiffrement et remplacement atomique du fichier cible
```

Le chargement produit un modèle mémoire déchiffré. Les opérations modifient ce modèle; aucune écriture n'a lieu avant l'appel explicite à `save()`.

## Composants principaux

### Entrées/sorties et chiffrement

Les fonctions de chiffrement valident l'en-tête PTX, transforment une copie des données et préservent le tampon de l'appelant. Les chemins `str` et `os.PathLike` textuels sont normalisés; les erreurs système gardent leur type natif. Les écritures utilisent un fichier temporaire et `os.replace()`.

### `PTBlock` et parseur

`PTBlock` représente un bloc binaire, ses données brutes et ses enfants. Le parseur conserve dans l'ordre les blocs reconnus et les segments opaques. La profondeur, les cycles, les tailles et l'unicité des offsets sont bornés afin qu'un fichier mal formé produise une erreur contrôlée.

### `TimecodeEngine`

`TimecodeEngine` centralise les conversions entre échantillons, positions et durées. Il utilise la fréquence et la cadence réellement lues dans la session; aucune valeur temporelle par défaut n'est injectée silencieusement.

### `ProToolsSession`

`ProToolsSession` est la façade de haut niveau. Elle possède :

- les octets déchiffrés et le chemin absolu de la session;
- l'arbre racine ordonné;
- les métadonnées temporelles;
- la liste des offsets de blocs supprimés qui devront être purgés à la sauvegarde.

Les méthodes de lecture exposent pistes, marqueurs, clips et événements. Les méthodes de mutation couvrent les opérations audio documentées dans le README. Les validateurs communs résolvent les noms, compteurs, placements, géométries et dictionnaires avant toute écriture.

## Modèle de modification

Les mutations suivent trois règles générales :

1. **Ciblage déterministe** : un nom ou placement ambigu est refusé; les espaces d'IDs audio, fade et Clip Group restent séparés.
2. **Préservation binaire** : les zones inconnues et segments bruts sont conservés; aucun padding ou bloc n'est inventé hors d'un modèle validé.
3. **Transaction mémoire** : une opération composée sauvegarde l'arbre courant et le restaure entièrement si une étape échoue.

Un bloc existant conserve son `original_offset`, utilisé pour relocaliser les pointeurs. Une nouvelle copie reçoit un offset neuf. Une suppression réelle enregistre d'abord tous les offsets descendants afin que les références correspondantes soient retirées proprement.

## Sauvegarde

La sauvegarde suit quatre responsabilités logiques :

1. Calculer la nouvelle position de chaque bloc existant.
2. Purger et relocaliser la table globale des pointeurs.
3. Mettre à jour le pointeur initial vers cette table.
4. Sérialiser, rechiffrer et remplacer atomiquement la destination.

Avant l'écriture, les invariants structurels et temporels sont revalidés. Après succès, les données, offsets et chemin de l'objet courant correspondent au fichier sauvegardé. En cas d'échec, l'état mémoire et l'ancienne destination sont préservés.

## Invariants architecturaux

- Les payloads métier sont actuellement pris en charge uniquement en little-endian.
- Les structures racines essentielles doivent être uniques, correctement ordonnées et cohérentes.
- Les compteurs doivent correspondre au nombre réel de blocs ou d'enregistrements.
- Les données opaques sont conservées byte-for-byte.
- Les offsets existants ne sont jamais effacés globalement.
- Les nouvelles fonctions doivent réutiliser les validateurs et mécanismes transactionnels communs.
- La bibliothèque n'écrit pas dans `stdout`; les diagnostics facultatifs passent par le logger `pt_api`.
- Toute nouvelle disposition binaire doit être confirmée par une session Pro Tools de référence avant d'être déclarée prise en charge.

## Validation et évolution

La suite automatisée constitue le premier niveau de validation. Les mutations binaires nouvelles exigent ensuite une comparaison avec une session Pro Tools de référence et, lorsque nécessaire, une ouverture manuelle dans Pro Tools.

Une révision qui change le format, une fonctionnalité ou une limitation doit mettre à jour conjointement :

- le code et ses tests;
- `pt_format_specs.md` pour le comportement binaire;
- `README.md` pour le contrat public;
- `changelog.md` pour l'historique;
- `architecture.md` seulement si les composants ou le flux global changent;
- `handoff.md` pour l'état opérationnel de la prochaine reprise.
