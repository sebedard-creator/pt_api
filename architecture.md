# Architecture du Projet: pt_api

## Stack Technique
- **Langage principal :** Python 3
- **Format de sortie :** JSON (exhaustif)
- **Dépendances externes :** Aucune (librairie standard uniquement)

## Structure des Fichiers (état actuel)
- `pt_api.py` : **(Cœur de l'application)** API Python autonome pour modifier "en place" la session Pro Tools. Gère le décryptage/cryptage, les marqueurs, mutes, volume, renommage, trimming, fondus (In/Out/Crossfade) et Clip Groups (Lecture/Suppression).
- `architecture.md`, `changelog.md`, `handoff.md` : Documentation de session.
- `pt_format_specs.md` : Documentation permanente des découvertes binaires.

## Conventions
- **Sécurité :** Tous les secrets dans `.env`, jamais en dur dans le code.
- **Backend First :** Pas d'interface graphique avant un parseur et une API stables.
- **Propreté :** Code minimaliste. Aucune fonction inutilisée ou dead code. Scripts de test éphémères supprimés après chaque phase.
- **Dépendances :** `requirements.txt` à jour à la racine du projet.

## API Python Directe (`pt_api.py`)

### Classes principales
- **`PTBlock`** : Bloc binaire. Attributs : `block_type`, `content_type`, `items` (list), `original_offset` (`-1` pour les nouveaux blocs).
- **`ProToolsSession`** : Charge, parse et sauvegarde une session.
  - *Méthodes publiques* : `get_tracks()`, `get_markers()`, `get_clips()`, `get_timeline_clips()`, `add_marker()`, `mute_clip()`, `move_clip()`, `rename_clip()`, `delete_clip_group()`, `add_volume_node()`, `add_fade()`, `add_crossfade()`, `split_clip()`, `save()`.
  - *Méthodes internes clés* : `_ensure_fade_cache(track_name)` (injection du cache `0x2077`, **toujours** utilisée pour ça — ne jamais la redupliquer inline, cf. régression Phase 8), `_collect_offsets_recursive()` / `_purge_0002_records()` (voir règle de suppression ci-dessous).

### Système de sauvegarde (`save()`) — 4 passes
1. **Simulation** : `to_bytes()` calcule les offsets absolus. `global_mapping = {old_offset → new_offset}` pour les blocs ayant `original_offset > 0`.
2. **Reconstruction ciblée de `0x0002`** : `_rebuild_0002()` patche uniquement les pointeurs connus via le mapping.
3. **Mise à jour du pointeur `0x0001 → 0x0002`**.
4. **Sérialisation finale + re-chiffrement XOR**.

### Règles critiques pour l'édition de la timeline

**Ne JAMAIS retirer (`pop()`) un événement existant de la timeline — ni aucun bloc ayant un `original_offset > 0`.**
Ce bloc possède un pointeur dans la table `0x0002`. Le retirer sans rien faire d'autre crée un pointeur fantôme (dangling pointer) qui provoque l'erreur "Magic ID does not match", puisque `_rebuild_0002()` ne fait que *patcher* les pointeurs des blocs encore présents dans `global_mapping` — il ne supprime jamais un enregistrement de lui-même.

Deux solutions valides, selon le cas :
- **Remplacement (ex: `split_clip()`) :** Muter l'événement original sur place (changer son `clip_id`, son timestamp, etc.) et insérer les nouveaux événements à la suite. Le pointeur original se met à jour correctement dans `0x0002` au prochain `save()`.
- **Suppression pure, sans remplacement (ex: `delete_clip_group()`) :** Il n'y a rien à muter. Utiliser `self._collect_offsets_recursive(block, self._removed_offsets)` **avant** de faire le `.remove()`/`.pop()`, pour chaque bloc retiré. `save()` appelle ensuite `_purge_0002_records()` (avant `_rebuild_0002()`) pour retirer complètement les enregistrements correspondants de `0x0002`, au lieu de laisser un pointeur périmé. `_removed_offsets` est réinitialisé à chaque `save()`.

### Construction des queues de clips scindés (`0x2628`)

Lors d'un `split_clip()`, les sous-clips (`-01`, `-02`) ont une queue binaire différente du parent :
- **La zone metadata (UUID) doit être remplie de zéros**, pas copiée du parent.
- **Les timestamps `ts1`/`ts2` dans le `0x2628` du clip `-02` sont des timestamps ABSOLUS DE TIMELINE** (ex: `orig_abs_ts + cut_samples`), **PAS** des offsets internes (ex: `orig_ts1 + cut_samples`).
- **Le padding du clip `-01`** : `ff*8` + `fe` (byte séparé dans la zone suivante).
- **Le padding du clip `-02`** : `ff*8` (sans `fe` — le `fe` apparaît dans la zone "rest" après le padding).

## Découvertes Clés du Format PTX

### Bloc `0x0002` (table de pointeurs)
- Suite d'enregistrements structurés (typiquement 34 bytes).
- **Structure :** `[préfixe 8B: 00 00 00 01 04 00 01 00] [pointeur 32-bit LE] [métadonnées 22B]`
- `save()` met à jour les pointeurs existants mais ne crée pas de nouvelles entrées. Pour les Fade In/Out et les Crossfade, Pro Tools accepte que les nouveaux blocs n'aient pas d'entrée dans `0x0002`.

### Trimming & Clips (`0x262a` / `0x2629` / `0x2628`)
- **Encodage 24-bits vs 32-bits** : Identifié par un drapeau (`01 00` = sans offset source, `01 30` = avec offset source 24-bit).
- **Clip `-01` (première moitié)** : Drapeaux `01 00`, longueur 32-bit, ts1/ts2 hérités du parent, zone metadata = zéros.
- **Clip `-02` (deuxième moitié)** : Drapeaux `01 30`, offset source 24-bit, longueur 24-bit, ts1/ts2 = timeline absolus, zone metadata = zéros.
- **Limitation** : 24-bit non-signé = max 16 777 215 samples ≈ 5.8 minutes à 48kHz.

### Timeline (`0x1054` / `0x1052` / `0x1050` / `0x104f`)
- **`0x104f` du fade event** : `block_type=10` (pas 3), `clip_id` pointe vers le **clip parent** (pas le sous-clip), payload = **exactement 35 bytes**. Règle valide pour **tout** fade event, qu'il vienne de `add_fade()` (fade In/Out standalone) ou `add_crossfade()` — ne jamais le construire par `copy.deepcopy()` d'un événement Audio existant sans repatcher `block_type` et la longueur du payload : c'est exactement la régression corrigée en [0.6.1].
- **Crossfade** = 3 événements dans `0x1052` :
  1. Audio event (`clip_id` = `-01`, `bt=10`, secondary = `000101`)
  2. Fade event (`clip_id` = parent, `bt=10`, secondary = `010101`, timestamp = point de coupe)
  3. Audio event (`clip_id` = `-02`, `bt=10`, secondary = `000101`, timestamp = point de coupe)

### Géométrie des Fondus (`0x262f`)
- Payload 36B dans `0x2630`. Offset +8 = pré-roll (24-bit samples). Offset +11 = durée totale (24-bit samples).

### Cache des Fondus (`0x2077`)
- 293 bytes de payload (302B sérialisé), contient `0x2506` + `0x4826`×2 + `0x4827` + 8 bytes trailing padding.
- **Attention** : Le parser de `_parse_block()` consomme 2 bytes du trailing. Le hex hardcodé dans `_ensure_fade_cache()` inclut 2 bytes supplémentaires pour compenser.

### Clip Groups (`0x262c` / `0x262b` / `0x2428`)
- **Zone Secrète (`0x262c`)** : Contrairement aux clips standards (`0x262a`), les Clip Groups vivent dans un conteneur racine séparé appelé `0x262c`.
- **Bloc de Groupe (`0x262b`)** : Contient un sous-bloc `0x2628` (nom, drapeaux) et un payload `0x2523` qui stocke le timestamp absolu de départ du groupe sur la timeline.
- **Timeline Cachée (`0x2428`)** : Pro Tools génère un bloc racine `0x2428` qui embarque une timeline complète (`0x1054` -> `0x1052`). Les événements originaux des sous-clips sont déplacés de la piste principale vers cette piste cachée.
- **Événement Principal** : Un seul événement (pointant vers l'ID du groupe dans `0x262c`) est placé sur la piste audio principale (`0x1052`).

### Clip Gain (`0x2637` et `0x2628`)
- **Dictionnaire Global (`0x2637`)** : Le Clip Gain est global au fichier, stocké dans le bloc racine `0x2637`. Ce bloc est une **liste continue de points d'automation**.
  - Format : Un compteur 32-bit LE (Nombre de points), suivi d'autant de points de **30 octets**.
  - Structure d'un point : 26 octets de métadonnées + 4 octets de valeur (Float32 LE).
- **L'Interrupteur (`0x2628`)** : Pour relier un point d'automation à un clip, Pro Tools utilise les 4 octets situés à la **toute fin absolue** du payload du bloc `0x2628` (offset `len(payload) - 6`). 
  - Une valeur de `-1` (`FF FF FF FF`) signifie que le clip n'a pas de Clip Gain.
  - Toute autre valeur (ex: `00 00 00 00` pour Index 0) pointe vers l'index d'un **point** dans le bloc `0x2637`.
- **Valeurs de Gain (Float32 Little Endian) :**
  - `-10.0 dB` = `0xc86b20c1`
  - `+6.0 dB` = `0x0000c040`
  - `-inf dB` = `-290.21057` (`0xf41a91c3`)
