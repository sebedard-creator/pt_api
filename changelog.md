# Pro Tools API - Changelog

## Non publié - Audit du 2026-07-15

- **Chargement sûr** : validation de l'enveloppe PTX, du bloc spécial `0x0001`, de l'unique table `0x0002` finale et de toutes ses cibles standard avant d'exposer la session.
- **En-tête validé en amont** : contrôle commun de la signature, de la version, de l'endianness et du mode XOR avant déchiffrement ou parsing; un fichier manifestement invalide n'atteint plus le parseur.
- **Endianness explicite** : rejet immédiat des sessions déclarées big-endian, dont les payloads métier ne sont pas pris en charge de bout en bout, au lieu d'autoriser un chargement partiel trompeur.
- **Métadonnées temporelles sûres** : suppression du repli silencieux sur `48 kHz / 24 fps`; le chargement exige désormais des blocs racines `0x1028` et `0x204d` uniques et valides.
- **Sauvegarde sûre** : `save()` est transactionnel en mémoire et atomique sur disque; les relocalisations de pointeurs restent valides après plusieurs sauvegardes successives.
- **Préconditions de sauvegarde** : refus avant écriture d'un arbre sans bloc initial `0x0001`, sans table `0x0002` unique et finale, ou dont les métadonnées temporelles ne sont plus valides.
- **État post-sauvegarde cohérent** : mise à jour de `self.data` et de la représentation interne du pointeur `0x0001` après succès; l'objet courant correspond désormais exactement au fichier remplacé.
- **XOR sûr** : la transformation commune aux modes `0x01` et `0x05` travaille sur une copie. `xor_session()` écrit atomiquement et préserve une destination existante en cas d'écriture courte.
- **Sérialisation sûre** : `PTBlock.to_bytes()` valide les champs, les types d'items, les cycles et l'unicité globale des `original_offset` avant toute écriture.
- **Racines sérialisées uniformément** : factorisation des deux passes de sauvegarde dans un itérateur commun, conservation des segments racines `bytes` comme des `bytearray`, et rejet explicite des types inconnus avant écriture.
- **Table `0x0002` sans perte** : centralisation de la reconstruction de son payload plat, prise en charge identique de `bytes` et `bytearray`, et suppression de l'ancienne branche morte consacrée aux blocs fantômes désormais interdits par le parseur.
- **Compteurs `0x0002` validés** : chaque série consécutive de pointeurs doit posséder un compteur UInt16 BE présent et exact au chargement comme avant sauvegarde; les tables susceptibles de provoquer `End of stream` sont refusées tôt.
- **Parseur borné** : reconnaissance correcte des blocs vides en fin de conteneur, rejet de `block_size = 1` et limite explicite de 128 niveaux d'imbrication.
- **Payloads fixes protégés** : les enregistrements connus `0x0002`, `0x1028`, `0x104f`, `0x204d`, `0x260a`, `0x262f` et `0x2637` restent plats même si leurs octets contiennent une fausse entête `0x5A` plausible.
- **Bloc spécial `0x0001` isolé** : son pseudo-`content_type`, formé par le mot faible du pointeur vers `0x0002`, ne peut plus usurper une table, une métadonnée, une timeline ou une liste de clips. Les collisions synthétiques et deux sauvegardes successives sont couvertes.
- **Structure du projet** : suppression d'un dépôt Git imbriqué accidentel `pt_api/` qui ne contenait aucun code et pouvait perturber Git, les IDE ou les outils de construction. Le module installable reste `pt_api.py`.
- **Montage robuste** : durcissement des opérations de renommage, duplication, trimming composé, fondus autonomes/combinés, crossfade, Clip Groups, automation, marqueurs et lectures de session. `set_clip_gain()` clone désormais un point partagé avant écriture; `add_volume_node()` privilégie le nom visible et valide intégralement la playlist ciblée.
- **Namespaces Clip Group/audio séparés** : `00 00 01` est classé comme macro de groupe et `00 01 01` comme placement audio dans toutes les lectures et mutations. Un ID de groupe égal à un ID de clip ne peut plus être muté, déplacé ou scindé par erreur.
- **Entrées numériques et texte bornées** : prise en charge de `-math.inf` pour le Clip Gain, rejet contrôlé des dépassements Float32/Int16/UInt64 et des conversions temporelles non représentables, validation UTF-8 uniforme des marqueurs et sous-clips.
- **Arbres mémoire bornés** : `PTBlock.to_bytes()` et `get_all_blocks()` partagent la détection des cycles et la limite de profondeur du parseur.
- **Marqueurs sur timeline valide** : `add_marker()` valide désormais le compteur, le nom et les événements de la map de pistes au lieu de tester seulement la présence superficielle d'un bloc.
- **Noms de pistes stricts** : le crossfade utilise désormais le validateur commun des playlists; suppression du décodage UTF-8 avec perte et rejet transactionnel des noms invalides, structures incohérentes ou pistes ambiguës.
- **Géométries uniques** : validation centralisée de l'unique racine `0x2630`, de son compteur et de ses payloads `0x262f`; les fondus, crossfades et lectures ne sélectionnent plus arbitrairement la première liste.
- **Nettoyage** : suppression de `wipe_all_offsets()`, ancien utilitaire public dangereux et inutilisé; toutes les méthodes privées restantes ont au moins un appel réel.
- **API silencieuse** : remplacement des impressions directes de chargement, sauvegarde et montage par le logger standard `pt_api`; aucune écriture dans `stdout` ne peut désormais perturber l'appelant après une sauvegarde réussie.
- **Chemins cohérents** : validation commune pour le constructeur, `unxor_session()`, `xor_session()` et `save()`; prise en charge de `pathlib.Path`, rejet précoce uniforme des chemins binaires, conservation absolue du chemin source et préservation des sous-types d'erreurs système.
- **Nettoyage statique** : suppression des trois dernières variables locales mortes; aucun import, local ou méthode privée inutilisé ne subsiste dans `pt_api.py`.
- **Tests** : couverture automatisée portée à 146 tests, complétée par des ouvertures manuelles dans Pro Tools, quatre no-op SHA-256 bit-perfect, les lectures publiques des sessions réelles, la compatibilité syntaxique Python 3.8 et la construction réussie du wheel PEP 517.
- **Documentation** : correction des signatures publiques, de l'encodage 24-bits et de l'identité réelle de `0x2077`; le README inventorie désormais toute la surface publique stable, distingue les utilitaires internes et explicite les limites de format, de ciblage et d'édition. `pt_format_specs.md` a été refondu en spécification normative complète du code 1.3.6 : 33 symboles publics, catalogue des blocs, algorithmes d'écriture, constantes binaires, limites et inventaire exhaustif des erreurs de l'API et des erreurs Pro Tools connues. `architecture.md` est désormais un survol global distinct de cette spécification, et `handoff.md` consigne l'état validé, les sessions de référence, les risques connus et la procédure des futures révisions; normalisation UTF-8 des fichiers Markdown.

## v1.3.6 (Hotfix Flags & 24-bit Length)

- **Bug Fix** : Les longueurs des sous-clips utilisant le flag `0x0001` n'étaient pas masquées à 24-bits (`& 0x00FFFFFF`). La lecture brute 32-bits absorbait le quatrième octet et produisait des longueurs de plusieurs milliards de samples.
- **Bug Fix** : Ajout du flag `0x2001`, qui suit le même encodage (`src_offset` et `length` sur 24-bits) que `0x3001`.

## v1.3.5 (Hotfix Phantom Pointers)

- **Bug Fix Critique (End of Stream / Phantom Blocks)** : Le parser analysait récursivement les données plates de `0x0002` lorsqu'un pointeur ressemblait fortuitement à une entête de bloc. La sauvegarde amputait ensuite la table d'indexation. `_parse_block()` n'analyse désormais plus d'enfants dans `0x0002`.

## v1.3.4 (Hotfix)

- **Bug Fix Critique (End of Stream)** : Suppression d'un padding destructeur. Pro Tools n'aligne pas les blocs `0x2628` de taille impaire avec `\x00`; l'ancien ajout décalait les structures binaires suivantes et corrompait notamment certains renommages de clips.

## [1.1.2] - 2026-07-14

- **Robustesse** : Amélioration de l'extraction de `length` et `src_offset` dans `get_timeline_clips()` par lecture formelle du flag UInt16.

## [1.1.1] - 2026-07-14

- **Feature Update** : `get_timeline_clips()` retourne `src_offset_samples`, extrait dynamiquement de `0x2628`.

## [1.1.0] - 2026-07-14

- **Feature** : Ajout de `get_timeline_clips()` pour lire les positions absolues des événements de timeline.

## [1.0.1] - 2026-07-11

- **Fix** : Correction de régressions `NameError` dans `add_fade()` et `add_volume_node()`.
- **Fix** : `gen_xor_delta()` lève une exception si la clé cryptographique est introuvable.
- **Fix** : `split_clip()` filtre la piste lors de la recherche du clip d'origine.
- **Cleanup** : Suppression de code mort et d'imports dupliqués.

## [1.0.0] - 2026-07-11

- **Initial Commit** : Première version publique de l'API autonome.
