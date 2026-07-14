# Pro Tools API - Changelog

## [1.0.0] - 2026-07-11
- **Initial Commit** : Première version publique officielle de l'API. L'architecture est stable, les blocs essentiels (`0x262a`, `0x2628`, `0x1050`, etc.) sont déchiffrés et l'API est entièrement validée.

## [1.0.1] - 2026-07-11
- **Fix** : Correction des régressions (`NameError: find_blocks`) dans `add_fade()` et `add_volume_node()`.
- **Fix** : Gestion des erreurs dans `gen_xor_delta()` avec levée d'une exception si la clé cryptographique est introuvable.
- **Fix** : `split_clip()` filtre désormais correctement la piste lors de la recherche du clip d'origine, évitant les erreurs de découpage avec les clips copiés sur de multiples pistes.
- **Cleanup** : Suppression du code mort et des imports dupliqués. Validation complète de l'absence de conflit entre les caches de fondu (0x2077) et les marqueurs.

## [1.1.2] - 2026-07-14
- **Bug Fix / Robustness** : Amélioration de l'extraction de `length` et `src_offset` dans `get_timeline_clips()`. L'API utilise désormais un `unpack_from("<H")` pour lire formellement le flag UInt16 au lieu de se fier à une signature stricte de 5 octets qui échouait en présence de padding imprévu.

## [1.1.1] - 2026-07-14
- **Feature Update** : La méthode `get_timeline_clips()` retourne désormais la clé `src_offset_samples`, extrayant dynamiquement le décalage source des sous-clips (flag `0x3001` / `0x01 0x30`) stocké dans le bloc `0x2628`. Crucial pour l'alignement audio.

## [1.1.0] - 2026-07-14
- **Feature** : Ajout de la méthode `get_timeline_clips()`. Cette méthode parcourt la carte des pistes (`0x1054` -> `0x1052`) et renvoie la position absolue exacte (timecode) de tous les événements placés sur la timeline, permettant des opérations complexes d'alignement.
