# Spécifications Binaire du Format Pro Tools (.ptx)
*(Sessions analysées en 23.98, 24 et 29.97df fps)*

## 1. Structure Générale du Fichier

```
[En-tête 20 octets] [Bloc 0x0001] [Bloc racine 1] ... [Bloc racine N] [Bloc 0x0002]
```

- **En-tête (20 octets) :** Identifiant Avid, version, endianness.
- **Bloc `0x0001` :** Premier bloc. Payload = 1 entier 32-bit LE pointant vers l'offset absolu du bloc `0x0002`.
- **Blocs racines :** Séquence de blocs `0x5A` imbriqués contenant les données de session.
- **Bloc `0x0002` :** Dernier bloc. Table des pointeurs de la session.

### Format d'un Bloc Binaire (`0x5A`)

```
0x5A | block_type (2 octets LE) | block_size (4 octets LE) | content_type (2 octets LE) | payload...
```

- **`block_size`** : Inclut les 2 octets du champ `content_type` et les octets du payload.
- **Bloc vide** : Un bloc sans payload omet le champ `content_type`. Sa taille `block_size` est `0` (taille totale du bloc = 7 octets).
- **Imbrication** : Les blocs enfants sont définis par la présence de l'octet `0x5A` dans le payload du parent.
- **Blocs fantômes** : Si un marqueur `0x5A` apparaît comme donnée brute (ex: dans le payload de `0x0002`), il aura généralement `block_type=2`, `content_type=0`, `size=0`. Ces octets doivent être traités comme des données brutes (7 octets), et non développés comme des blocs réels.

---

## 2. Bloc `0x0002` — Table de Pointeurs

Le bloc `0x0002` est une suite d'enregistrements structurés de taille variable. Il ne s'agit pas d'un tableau brut 64-bit.

**Format d'un enregistrement `0x0002` :**
```
[Préfixe 8 octets] [Pointeur 32-bit LE] [Métadonnées variables]
```

- **Préfixe :** Généralement `00 00 00 01 04 00 01 00` (8 octets).
- **Pointeur :** Offset absolu (entier non signé 32-bit LE) pointant vers un bloc cible (racine ou enfant) dans le fichier binaire.
- **Métadonnées :** Octets variables (habituellement 22 octets) décrivant le `content_type` ou les flags de l'objet pointé.
- **Intégrité :** Tout retrait ou modification d'un bloc pointé par `0x0002` requiert la mise à jour de son pointeur, ou la purge complète de son enregistrement de 34 octets dans le bloc `0x0002`. Un pointeur orphelin déclenche une erreur fatale à la lecture ("Magic ID does not match").

---

## 3. Pistes et Événements de Timeline

| Content Type | `block_type` | Description |
|---|---|---|
| `0x2030` | Variable | Bloc racine d'une piste. Contient les blocs de timeline (`0x1054`), cache de fondus (`0x2077`) et padding. |
| `0x1054` | 2 | Map d'événements de la piste. Contient le conteneur `0x1052`. |
| `0x1052` | 3 | Playlist de la piste. Header : `[name_len 4B][name][count 4B LE]`. Contient `count` événements `0x1050`. |
| `0x1050` | 3 | Conteneur d'un événement sur la timeline. Contient `0x104f` + payload secondaire (3 octets). |
| `0x104f` | Variable | Événement spécifique (Audio ou Fade). Voir structure ci-dessous. |

### Structure du Payload `0x104f` (Événement Timeline)

| Offset | Taille | Type | Description |
|---|---|---|---|
| +0 | 1 | Byte | Flag mute : `0x00` (actif), `0x01` (muté). |
| +2 | 4 | UInt32 LE | **Clip ID** : L'index (0-based) du clip racine `0x2629` dans la liste globale `0x262a`. |
| +7 | 8 | UInt64 LE | **Timestamp Absolu** : Position de l'événement sur la timeline (en samples). |
| +15 | 1 | Byte | **Type d'Événement** : `0x01` (Fondu), `0x03` (Audio). |

**Payload Secondaire (à la fin de `0x1050`) :**
- Audio (`block_type=3`) : `00 01 01` (3 octets).
- Fondu (`block_type=10`) : `01 01 01` (3 octets).

---

## 4. Dictionnaire des Clips Audio (`0x262a`)

| Content Type | `block_type` | Description |
|---|---|---|
| `0x262a` | Variable | Bloc racine contenant la liste globale des clips de la session. |
| `0x2629` | 11 | Définition d'un clip. Contient `0x2628`, UUID, timestamps, etc. |
| `0x2628` | 4 | Nom du clip et métadonnées d'attributs. |

### Découpage (Trimming) et Création de Sous-Clips

Lorsqu'un clip est coupé, Pro Tools génère des sous-clips virtuels.
- **Indexation** : Le Clip ID utilisé dans la timeline (`0x104f`) est strictement défini par la position ordinale du bloc `0x2629` au sein de la liste `0x262a`.
- **Encodage de Trimming** : Les informations de longueur et de décalage des sous-clips sont encodées à la fin du payload du bloc `0x2628`.
- **Drapeaux 24-bits vs 32-bits** :
  - `01 00` : Sans offset source (Clip original ou première moitié de la découpe).
  - `01 30` : Avec offset source 24-bits (Deuxième moitié de la découpe). La limite théorique du format 24-bits est `16 777 215` samples.

**Règle de Scission (`split`) :**
1. **Clip de Gauche (`-01`)** : Drapeaux `01 00`, longueur 32-bits. Timestamps hérités du parent. Zone métadonnées remplie de zéros. Padding de fin : `ff*8 + fe`.
2. **Clip de Droite (`-02`)** : Drapeaux `01 30`, offset source 24-bits, longueur 24-bits. Timestamps modifiés en valeurs absolues de timeline. Padding de fin : `ff*8` seulement.

---

## 5. Géométrie et Injection des Fondus (Fades)

| Content Type | `block_type` | Description |
|---|---|---|
| `0x2630` | 1 | Racine stockant la liste des géométries de fondus. Commence par un UInt32 LE (compteur). |
| `0x262f` | 2 | Géométrie d'un fondu. Payload de 22 octets. Offset 8 (UInt16 LE) = longueur (samples). Offset 11 (Byte) = courbe (`0x01` = Equal Power). |
| `0x2077` | 18 | Cache de fondus de piste. Requis pour le rendu visuel et audio. |

**Structure du Cache `0x2077` :**
- Payload principal (ex: 148 octets).
- Sous-blocs obligatoires imbriqués : `0x2506`, `0x4826` (×2), `0x4827`.
- Padding de fin (typiquement 8 octets).

**Règles d'Injection sur la Timeline (`0x1052`) :**
1. **ID Racine** : L'événement de fondu dans `0x104f` doit strictement pointer vers l'index `clip_id` du clip parent complet, jamais vers un sous-clip découpé.
2. **Ordre Chronologique** : Les événements `0x1050` (audio et fondus) doivent être triés par leur timestamp (offset +7).
3. **Identification** : Pro Tools apparie un événement fondu au bon fragment de clip en croisant leurs timestamps absolus respectifs.

---

## 6. Automation de Gain de Clip (Clip Gain)

| Content Type | `block_type` | Description |
|---|---|---|
| `0x2637` | 1 | Racine stockant la liste globale des points d'automation de Clip Gain. |

### Dictionnaire Global des Points (`0x2637`)
Le Clip Gain est stocké indépendamment de la timeline, sous forme de liste continue de points.
- **En-tête** : 4 octets (UInt32 LE) définissant le nombre total de points.
- **Points (30 octets chacun)** :
  - Métadonnées (26 octets).
  - Valeur d'amplitude (4 octets, Float32 LE).

### L'Index d'Automation (`0x2628`)
Pour lier un clip à son point d'automation, Pro Tools lit un index de 4 octets situé **strictement à la fin absolue** du payload du bloc `0x2628` du clip (offset : `len(payload) - 6`).
- **Valeur `-1` (`FF FF FF FF`)** : Aucun Clip Gain actif.
- **Valeur `>= 0`** : Index du point d'automation dans le tableau `0x2637`.

**Valeurs d'Amplitude Courantes (Float32 LE) :**
- `0.0 dB` : (Généralement non-instancié, index `-1`)
- `-10.0 dB` : `0xc86b20c1`
- `+6.0 dB` : `0x0000c040`
- `-inf dB` : `-290.21057` (`0xf41a91c3`)

---

## 7. Groupes de Clips (Clip Groups)

| Content Type | `block_type` | Description |
|---|---|---|
| `0x262c` | Variable | Racine contenant la liste des définitions de groupes. Indépendant de `0x262a`. |
| `0x262b` | Variable | Définition d'un groupe. Contient un `0x2628` (nom) et un `0x2523` (timestamp de départ). |
| `0x2428` | Variable | Racine de timeline cachée. Stocke les composants internes du groupe. |

- Les composants d'un groupe (clips audio) sont déplacés de la piste principale vers une timeline cachée imbriquée dans `0x2428`.
- Un événement macro unique est placé sur la piste principale (`0x1052`), pointant vers l'ID du groupe au sein de la liste `0x262c`.

---

## 8. Fonctionnalités Non Explorées (Limites de l'API)

Les éléments suivants n'ont pas encore été rétro-ingénieriés et ne sont pas supportés par l'API actuelle :
- **Données MIDI** : Les régions MIDI et les contrôleurs continus.
- **Inserts et Routing** : Plugins, envois (sends), affectations d'E/S (I/O).
- **Autres Automations** : Panoramique (Pan), Mute automation, ou automation de plugins. Seules l'automation de Volume de Piste (`0x260a`) et le Gain de Clip Statique (`0x2637`) sont supportés.
- **Vidéo** : Pistes et clips vidéo.

---

## 9. Codes d'Erreur Pro Tools

| Erreur Observée | Explication Technique | Solution de Réparation |
|---|---|---|
| **Magic ID does not match** | Un pointeur 32-bit dans l'en-tête `0x0001` ou dans la table `0x0002` pointe vers un octet ne correspondant pas à la signature `0x5A` ou au type de bloc attendu. | Les offsets absolus ont été modifiés. La méthode de sauvegarde doit repasser sur tous les enregistrements `0x0002` pour corriger les pointeurs affectés par un décalage de payload. |
| **Unexpected stream type** | Conflit entre le `block_type` et le `content_type` déclarés dans un bloc binaire. | Vérifier l'ordre de sérialisation de l'en-tête de bloc (Type, Taille, Content). |
| **End of stream** | Le compteur d'un conteneur itératif (`0x262a`, `0x2630`, `0x1052`) est plus grand que le nombre de blocs enfants réellement présents dans le payload. | Lors de l'injection ou suppression, s'assurer que le UInt32 LE en tête de conteneur est mis à jour. |
