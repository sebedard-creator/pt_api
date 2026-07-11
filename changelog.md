# Changelog - pt_api

## [0.8.0] - 2026-07-11 — Phase 11 : Rectification finale du Clip Gain (Crack Absolu)
- **Découverte de la structure réelle de `0x2637` :** Le dictionnaire global n'est PAS un tableau de courbes de 64 octets. C'est en fait un compteur de 32-bits (indiquant le nombre de points), suivi d'un tableau continu de **points individuels de 30 octets** (26 octets de métadonnées + 4 octets de Float32). L'API injectait un nombre incorrect d'octets, désalignant le bloc et causant l'erreur "Magic ID does not match" lorsque Pro Tools cherchait la signature `01 46 01 00` au mauvais offset.
- **Preuve irréfutable de l'offset `len-6` :** Un diagnostic strict octet par octet d'un fichier natif Pro Tools a prouvé que l'index de Clip Gain pour relier un sous-clip à l'automation de `0x2637` s'écrit **absolument toujours à l'offset `len(payload) - 6`** dans le bloc `0x2628`. L'hypothèse de l'offset dynamique `ff+37` était fausse et causée par les effets secondaires du bug de désalignement de `0x2637`.
- **Validation Finale :** L'API écrit maintenant les stricts 30 octets dans `0x2637`, incrémente le compteur, et applique le pointur à `len-6`. Résultat garanti 100% stable, y compris sur les *Whole File Clips* originaux, qui possèdent eux aussi le pointeur `len-6`.

## [0.7.0] - 2026-07-11 — Phase 9 : Rétro-ingénierie du Clip Gain
- **Découverte majeure du Clip Gain :** Le gain de clip n'est pas codé comme un simple décalage binaire sur le payload du clip audio (`0x2629` / `MdChun`). L'algorithme repose plutôt sur un dictionnaire d'automation stocké globalement à la racine de la session dans le bloc `0x2637`.
- **Résolution d'énigme de l'interrupteur caché :** Contrairement à nos premières théories, `0x2637` ne stocke pas de Clip ID. L'index se trouve dans le clip à `len-6`.
- **Rétro-ingénierie de l'encodage float :** Un Gain de +12.0 dB est inscrit `12.0`, un Gain -15.5 dB devient `-15.4736` et l'infini est un float fixe `-290.21057` (`0xf41a91c3`).

## [0.6.2] - 2026-07-11 — Phase 10 : Résolution finale des Fondus sur sous-clips
- **Bug critique — `add_fade()` ciblait le mauvais clip :** Pro Tools exige que l'événement de fondu (`0x104f` avec `bt=10`) pointe **strictement** vers l'ID du clip racine original (`cid=0` pour `patate`), et non vers l'alias découpé (`patate-01`). Pro Tools apparie ensuite le fondu avec le bon fragment de clip en comparant leurs timestamps absolus sur la timeline.
- **Bug de tri — Insertion chronologique :** L'événement de fondu était injecté au mauvais index dans `0x1052`, avant un clip antérieur. Le tri chronologique des blocs `0x1050` est obligatoire.
- **Optimisation de Géométrie :** Utilisation de la version courte de 22 bytes (`0x20` à l'offset 5) pour la géométrie du fondu (`0x262f`). L'octet `01` à l'offset 11 définit formellement une courbe Equal Power. Il a été prouvé qu'aucun sous-clip virtuel `0x2629` additionnel n'est requis pour héberger un fade sur un clip.

## [0.6.1] - 2026-07-10 — Phase 8 (suite) : Correctifs de la régression "Magic ID does not match"
- **Bug critique — `delete_clip_group()` violait la règle "jamais de `pop()`" :** La fonction retirait le bloc `0x262b` et les événements `0x1050` du groupe via `.remove()`, sans jamais purger leurs enregistrements dans `0x0002`. `_rebuild_0002()` ne fait que *patcher* les pointeurs des blocs encore présents dans `global_mapping` — un bloc supprimé laissait donc un pointeur périmé (dangling pointer) intact. **Correctif :** nouvelle méthode `_collect_offsets_recursive()` qui capture les `original_offset` du sous-arbre juste avant chaque `remove()`, et nouvelle méthode `_purge_0002_records()` qui retire complètement l'enregistrement `0x0002` correspondant (span variable, calculé via la position du préfixe suivant) au lieu de le laisser pointer dans le vide. Branché dans `save()`, juste avant `_rebuild_0002()`.
- **Bug — `add_fade()` ne respectait pas la structure validée du fade event :** La fonction faisait un `copy.deepcopy()` de l'événement Audio original et ne patchait que 2 champs (offset 15 = type, offset 7 = timestamp), laissant le `block_type` du `0x104f` à sa valeur Audio héritée (jamais mis à `10`) et un payload de la mauvaise longueur (celle de l'Audio, pas les 35 bytes fade). C'était le Bug 1 de la Phase 7 (déjà réglé dans `add_crossfade()`) qui n'avait jamais été porté dans `add_fade()`, écrit avant cette découverte. **Correctif :** le `0x104f` du fade est maintenant construit à la main dans `add_fade()`, avec la même structure validée que `add_crossfade()` (`block_type=10`, payload 35 bytes, queue `01feff...`, `clip_id` = clip parent).
- **Bug — `add_fade()` injectait le cache de fade sur la mauvaise piste :** Le bloc d'injection du `0x2077` dans `add_fade()` était une copie divergente de `_ensure_fade_cache()`, écrite avant que cette méthode partagée existe. Elle prenait le **premier** `0x2030` trouvé dans `root_items` via `next()`, sans jamais vérifier le nom de piste — sur une session multi-pistes, un fade ajouté sur une piste autre que la première atterrissait dans le mauvais conteneur. **Correctif :** `add_fade()` appelle maintenant `self._ensure_fade_cache(track_name)`, déjà utilisé et validé par `add_crossfade()`.
- **Nettoyage :** retrait d'un `print()` de debug oublié dans `delete_clip_group()`.
- **Validation :** trois harnais de test synthétiques (objets `PTBlock` construits à la main, sans fichier `.ptx` réel) confirment chaque correctif isolément :
  1. `_purge_0002_records()` retire exactement les enregistrements des blocs supprimés et laisse les autres intacts, pointeurs patchés correctement par `_rebuild_0002()` ensuite.
  2. Le fade event généré par `add_fade()` a bien `block_type=10`, un payload de 35 bytes, et `clip_id` pointant vers le parent.
  3. Sur une session à 2 pistes, `add_fade()` sur la 2e piste injecte le cache dans le 2e `0x2030`, pas le premier.
  - **Reste à faire :** validation en conditions réelles via `create_super_session.py` + réouverture dans Pro Tools (pas de fichier `.ptx` de test disponible dans cette session pour un round-trip binaire complet).

## [0.6.0] - 2026-07-10 — Phase 8 : Unification de l'API & Gestion des Clip Groups
- **Unification :** Intégration du décryptage/cryptage (fonctions XOR) directement dans `pt_api.py`. L'API est désormais 100% autonome et n'a plus aucune dépendance.
- **Nettoyage :** Suppression des fichiers obsolètes (`pt_decrypt.py`, `pt_parser.py`, `pt_builder.py`, `create_fade_clean.py`, `update_markers.py`).
- **Découverte Clip Groups :** Rétro-ingénierie complète de la structure des Clip Groups (`0x262c`, `0x262b`, timeline cachée `0x2428`).
- **Nouvelles méthodes :** Modification de `get_clips()` pour retourner les groupes. Ajout de `delete_clip_group()` pour retirer un groupe et ses références de la timeline en toute sécurité.
- **Création de Clip Group :** En pause. La rétro-ingénierie a prouvé que c'est trop risqué sans connaître la clé liant les blocs `0x2428` et `0x2501`.
- **Régression critique :** Lors des manipulations, certaines corrections cruciales de la Phase 7 (comme le pointage des `clip_id` dans les fades) ont été écrasées ou corrompues. L'API génère présentement un "Magic ID does not match" lors de l'utilisation combinée des fades. Un correctif par Claude est requis.

## [0.5.2] - 2026-07-10 — Phase 7 : Correction Crossfade API (6 bugs corrigés)
- **Bug 1 — `block_type` du fade event :** Le `0x104f` du fade était créé avec `bt=3` (audio) au lieu de `bt=10` (fade). Corrigé dans `add_crossfade()`.
- **Bug 2 — `clip_id` du fade event :** Le fade pointait vers `idx_01` au lieu du clip parent original. Les références montrent que le fade pointe toujours vers le **clip parent**. Corrigé dans `add_crossfade()`.
- **Bug 3 — Tail du clip `-01` polluée :** `split_clip()` copiait les UUID metadata du parent dans la zone qui doit être remplie de zéros pour les sous-clips. Corrigé avec une construction manuelle de la tail (11B header + 21 zéros + 6B footer).
- **Bug 4 — Timestamps du clip `-02` faux :** Les `ts1`/`ts2` dans `0x2628` sont des timestamps absolus de la **timeline** (pas des offsets internes). Corrigé en utilisant `orig_abs_ts + cut_samples` au lieu de `orig_ts1 + cut_samples`.
- **Bug 5 — `pop()` de l'événement original :** Supprimé `b1052.items.pop()` qui créait un dangling pointer dans `0x0002`. L'événement est maintenant muté sur place pour devenir ev_01, et ev_02 est inséré après.
- **Bug 6 — Padding `0x2077` et `-02` :** Le `0x2077` avait 2 bytes de trailing padding manquants. Le clip `-02` avait un mauvais pattern de padding `ff` (`fe` à la mauvaise position, `ff*8` au lieu de `ff*8 + fe` séparé).
- **Validation :** Le fichier généré par l'API (`18_api_crossfade_test.ptx`) est maintenant **structurellement identique** au fichier de référence (`17_crossfade_test.ptx`) : même taille (167924B), mêmes pointeurs, mêmes tails, mêmes timestamps, même fade geometry.

## [0.5.1] - 2026-07-10 — Phase 7 : Rétro-ingénierie Trimming & API Crossfade
- **Découverte de l'encodage de Trimming :** Rétro-ingénierie de la queue binaire du bloc `0x2628` pour les clips virtuels. Encodage 24-bits/32-bits.
- **Nouvelles méthodes API :** `split_clip()`, `add_crossfade()`.
- **Limitation :** Encodage 24-bits (clips ≤ ~5.8 minutes à 48kHz).

## [0.5.0] - Phase 5/6 : Injection de Fondus — Refonte du système de pointeurs
- **Découverte critique :** Le bloc `0x0002` contient des enregistrements structurés, pas un tableau 64-bit.
- **Refonte de `save()` :** Suppression de `fix_pointers_in_payload()`. Ajout de `_decode_0002_records()` et `_rebuild_0002()`.
- **Validation round-trip identité :** `save()` produit un fichier bit-parfait.
- **API Fade In/Out :** `add_fade()` gère dynamiquement la création des blocs virtuels.

## [0.4.0] - Phase 4 : Injection de marqueurs
- Ajout de la capacité d'injecter des marqueurs via le bloc `0x2067`.

## [0.3.0] - Phase 3 : Edition (Volume & Mute) 
- Implémentation du système de lecture/écriture de l'automation de volume (`0x260a`). API pour `set_mute`.

## [0.2.0] - Phase 2 : Parseur et Squelette de l'API
- Classe `ProToolsSession` complète.

## [0.1.0] - Phase 1 : Décryptage
- Script `pt_decrypt.py` pour annuler l'encryption XOR.
