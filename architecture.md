# Architecture logicielle de `pt_api` 1.4.2

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
    → éventuellement, clonage atomique d'un WAV et nouvelle identité BWF/PTX
    → sérialisation et relocalisation des pointeurs
    → rechiffrement et remplacement atomique du fichier cible
```

Le chargement produit un modèle mémoire déchiffré. Les opérations ordinaires modifient seulement ce modèle jusqu'à l'appel explicite à `save()`. Le relink physique est l'exception : il installe d'abord un clone WAV atomique — éventuellement avec le chunk PCM `data` d'un rendu strictement compatible — puis l'appelant sauvegarde séparément le PTX; cette frontière est explicite dans le contrat public.

Le lecteur isole les conventions Premiere dans `pt_api`, non dans les applications clientes : il reconnaît les headers `0x2106` variables et les géométries virtuelles observées, sans les modifier. `get_relink_write_status()` ajoute un préflight public observationnel : il peut signaler, avant toute création de WAV, qu'un placement virtuel utilise le header variable Premiere non pris en charge. L'écriture/relink de ces clips reste un point de recherche bloqué : les essais du 21 juillet 2026 ont été refusés par Pro Tools (`End of stream encountered`), y compris avec un catalogue hybride créé nativement.

Le builder de session audio constitue un second flux de haut niveau, volontairement étroit mais indépendant de l'application cliente :

```text
template.ptx + manifeste ordonné de WAV/pistes
    → validation complète du template, des descripteurs et des WAV
    → conservation de l'ordre fourni par le client
    → remplacement/clonage du média-prototype et des définitions de clips
    → spotting BWF ou explicite dans les pistes existantes, overlap conservé
    → copie byte-for-byte des WAV dans Audio Files
    → sauvegarde puis rechargement sémantique du PTX temporaire
    → renommage atomique du dossier de session complet
```

Contrairement au relink, `build_audio_session()` possède toute la transaction de livraison. Le dossier cible doit être absent; un échec supprime uniquement le répertoire temporaire créé par la fonction et ne publie aucun résultat partiel.

## Composants principaux

### Entrées/sorties et chiffrement

Les fonctions de chiffrement valident l'en-tête PTX, transforment une copie des données et préservent le tampon de l'appelant. Les chemins `str` et `os.PathLike` textuels sont normalisés; les erreurs système gardent leur type natif. Les écritures PTX et le clonage physique d'un WAV utilisent un fichier temporaire et `os.replace()`.

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

Les méthodes de lecture exposent pistes, marqueurs, clips et événements. Les méthodes de mutation couvrent les opérations audio documentées dans le README, y compris le clonage/relink physique étroit d'un placement. Les validateurs communs résolvent les noms, compteurs, placements, géométries, catalogues média et dictionnaires avant toute écriture. La hiérarchie interne d'un catalogue PTX reste distincte de la résolution des chemins : l'application fournit les chemins WAV, normalement sous le dossier `Audio Files` associé à la session.

La lecture des macros de Clip Groups suit une voie distincte : `get_timeline_clip_groups()` lit les occurrences visibles de la timeline dans leur namespace `0x262c`, sans les confondre avec les clips audio de `get_timeline_clips()`. Cette séparation préserve les identifiants indépendants et les placements répétés, tout en laissant l'API de groupe strictement en lecture seule.

### Builder de session audio

`build_audio_session()` est une façade de module au-dessus de `ProToolsSession`. Elle n'est pas un moteur général de création de sessions : elle exige un modèle natif 48 kHz/23,976 avec au moins une playlist visible, aucun événement de timeline visible ou caché et un média-prototype importé. Les helpers privés inspectent le RIFF/BWF, valident le manifeste ordonné, réécrivent le catalogue `0x1004`/`0x103a`, les entrées `0x1003`, les définitions `0x2629` et les événements `0x1050`, puis utilisent le pipeline `save()` existant.

Le profil d'écriture est choisi une seule fois depuis le prototype du template, avant toute validation de WAV : `native_float_15_142` (parent 15/142, durée UInt24) ou `native_float_31_151_u32` (parent 31/151, durée UInt32). Les octets non possédés par le writer restent ceux du prototype. `ProToolsSession.validate_audio_import_template()` expose ce diagnostic sans mutation et retourne le profil ainsi que les pistes visibles; les applications clientes n'ont donc pas à appeler les validateurs privés.

La séparation des responsabilités est stricte :

- le client décide de l'ordre, de la piste cible, du nom de clip et, facultativement, du filename livré;
- le BWF décide de la référence média et du timestamp de placement par défaut;
- un override explicite décide uniquement du timestamp de l'événement, sans falsifier la référence BWF;
- le chunk `data` décide de la durée;
- l'UMID BWF décide de l'identité média PTX;
- le template fournit exclusivement les structures opaques et constantes déjà produites par Pro Tools.

Les WAV livrés ne sont jamais réencodés ni enrichis par l'API. Ils sont copiés tels quels; les blocs `DGDA`, `minf` et `regn` ajoutés par Pro Tools ne sont pas reproduits par supposition.

Le relink physique suit le même principe de conservatisme : il ne reconstruit pas une géométrie audio. Sur un catalogue natif vérifié, il clone et retargete les layouts de production parent/virtuel `0x0000`, `0x0001`, `0x2000`, `0x2001`, `0x3000`, `0x3001` et `0x4001` en conservant leur flag et leur largeur d'offset source. La variante native Pro Tools/RX `0x3000 / 0x20 / 0x44 / 0x08` est également prise en charge avec son équation de référence incorporée vérifiée. Le préflight partage le validateur de géométrie du writer et ne déclare donc pas compatible un layout qu'il refuserait. Les headers virtuels Premiere à longueur variable restent explicitement bloqués; ils ne sont pas convertis heuristiquement.

Les deux records fixes d'une définition `0x2629` sont réassemblés lorsqu'une séquence fortuite a été parsée comme un bloc vide. La normalisation procède du span le plus tardif vers le plus ancien afin que la réduction d'un span scindé ne décale jamais l'index du suivant. Le rechargement de la livraison exige ensuite exactement 48 octets d'identité, 104 octets de lien média et un ID/index correspondant à l'ordinal de chaque clip.

Ce flux a été validé de bout en bout dans Pro Tools avec deux médias BWF distincts placés sur la même piste et se chevauchant d'un échantillon. Après sauvegarde et réouverture natives, les deux catalogues, définitions, liens physiques, longueurs et placements sont restés sémantiquement identiques.

## Modèle de modification

Les mutations suivent trois règles générales :

1. **Ciblage déterministe** : un nom ou placement ambigu est refusé; les espaces d'IDs audio, fade et Clip Group restent séparés.
2. **Préservation binaire** : les zones inconnues et segments bruts sont conservés; aucun padding ou bloc n'est inventé hors d'un modèle validé.
3. **Transaction contrôlée** : une opération composée sauvegarde l'arbre courant et le restaure entièrement si une étape échoue. Le relink supprime aussi son fichier temporaire; après son remplacement final réussi, le nouveau WAV appartient toutefois à l'appelant même si la sauvegarde PTX ultérieure échoue.

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
- Le builder audio doit rester limité au template et au WAV float validés; élargir les largeurs, formats, fréquences ou cadences exige de nouvelles références natives.

## Validation et évolution

La suite automatisée constitue le premier niveau de validation. Les mutations binaires nouvelles exigent ensuite une comparaison avec une session Pro Tools de référence et, lorsque nécessaire, une ouverture manuelle dans Pro Tools.

Une révision qui change le format, une fonctionnalité ou une limitation doit mettre à jour conjointement :

- le code et ses tests;
- `pt_format_specs.md` pour le comportement binaire;
- `README.md` pour le contrat public;
- `changelog.md` pour l'historique;
- `architecture.md` seulement si les composants ou le flux global changent;
- `handoff.md` pour l'état opérationnel de la prochaine reprise.
