# Pro Tools API - Changelog

## [1.0.0] - 2026-07-11
- **Initial Commit** : Première version publique officielle de l'API. L'architecture est stable, les blocs essentiels (`0x262a`, `0x2628`, `0x1050`, etc.) sont déchiffrés et l'API est entièrement validée.

## [1.0.1] - 2026-07-11
- **Fix** : Correction des régressions (`NameError: find_blocks`) dans `add_fade()` et `add_volume_node()`.
- **Fix** : Gestion des erreurs dans `gen_xor_delta()` avec levée d'une exception si la clé cryptographique est introuvable.
- **Fix** : `split_clip()` filtre désormais correctement la piste lors de la recherche du clip d'origine, évitant les erreurs de découpage avec les clips copiés sur de multiples pistes.
- **Cleanup** : Suppression du code mort et des imports dupliqués. Validation complète de l'absence de conflit entre les caches de fondu (0x2077) et les marqueurs.
