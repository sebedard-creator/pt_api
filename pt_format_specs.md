# Spécifications binaires du format Pro Tools (`.ptx`)

*(Spécification normative de `pt_api` 1.3.7; sessions de référence produites par Pro Tools Ultimate 2024.3.1 à 23.98, 24 et 29.97df fps.)*

Ce document décrit exactement les structures que le code courant lit, valide, modifie et sérialise. Une structure dite « observée » provient des sessions de référence; une structure dite « prise en charge » possède un chemin explicite dans `pt_api.py`. Les zones non interprétées sont conservées telles quelles et ne doivent pas être déduites par heuristique.

## 1. Enveloppe du fichier, chiffrement et parsing

### 1.1 Organisation générale

```text
[En-tête non chiffré de 20 octets]
[Bloc spécial 0x0001]
[Blocs et segments racines]
[Bloc racine final 0x0002]
```

L'en-tête occupe les offsets `0x00..0x13` :

| Offset | Taille | Valeur prise en charge |
|---|---:|---|
| `+0x00` | 1 | Signature `0x03`. |
| `+0x01` | 16 | Champ de version composé uniquement des octets ASCII `0` et `1`. |
| `+0x11` | 1 | Endianness : `0x00` little-endian, `0x01` big-endian. L'enveloppe accepte les deux valeurs, mais `ProToolsSession` rejette `0x01` avant le parsing des payloads métier. |
| `+0x12` | 1 | Mode XOR : `0x01` ou `0x05`. |
| `+0x13` | 1 | Valeur XOR servant à retrouver le delta. |

Un fichier de moins de 20 octets, une signature/version invalide, un drapeau d'endianness inconnu ou un mode XOR autre que `0x01`/`0x05` est rejeté avant l'analyse du corps.

### 1.2 Transformation XOR

Le chiffrement et le déchiffrement utilisent la même transformation symétrique et ne modifient jamais les 20 octets d'en-tête.

- `gen_xor_delta()` recherche le delta par balayage des 256 valeurs possibles.
- Mode `0x01` : le delta est l'entier `i` tel que `(i × 53) & 0xFF == xor_value`; l'index de clé est `offset & 0xFF`.
- Mode `0x05` : le delta est l'opposé modulo 256 de l'entier `i` tel que `(i × 11) & 0xFF == xor_value`; l'index de clé est `(offset >> 12) & 0xFF`.
- La table de clé contient `((index × delta) & 0xFF)` pour les 256 index.
- Chaque octet du corps, à partir de `0x14`, est XORé avec l'entrée correspondante.
- `unxor_session()` retourne une nouvelle `bytearray` déchiffrée. `xor_session()` transforme aussi une copie; le tampon fourni par l'appelant n'est pas muté.
- `xor_session()` écrit dans un fichier temporaire du dossier cible, vérifie les écritures courtes, puis installe la destination avec `os.replace()`; son retour normal est `None`.

### 1.3 Format générique d'un bloc

```text
0x5A | block_type UInt16 | block_size UInt32 | [content_type UInt16 | payload]
```

Tous les champs suivent théoriquement l'endianness de la session. Les payloads métier de l'API sont cependant définis uniquement en little-endian.

- `block_size` compte les deux octets de `content_type` et le payload, mais pas les sept premiers octets de l'en-tête.
- Un bloc vide possède `block_size = 0`, aucun `content_type` physique et une taille totale de 7 octets.
- `block_size = 1` est impossible : un bloc non vide doit contenir au moins le `content_type` de deux octets.
- Le champ physique `block_type` est UInt16. Le parseur ne reconnaît comme bloc que les valeurs `0x0000..0x00FF`; un octet haut non nul fait traiter la séquence comme donnée brute. `PTBlock.to_bytes()` applique la même borne 8-bit.
- `content_type` doit tenir sur UInt16; tailles et offsets sérialisés doivent tenir sur UInt32.
- Un `PTBlock` contient uniquement des `bytes`, `bytearray` ou d'autres `PTBlock`.
- `original_offset > 0` identifie un bloc chargé et doit être unique dans tout l'arbre. Les nouveaux blocs utilisent `-1` ou `0`.
- Parsing, sérialisation et `get_all_blocks()` sont limités à 128 niveaux et rejettent les cycles.

Les helpers `u_endian_read2(buf, offset, is_bigendian)` et `u_endian_read4(...)` lisent respectivement un UInt16 et un UInt32 selon l'endianness demandée. Ils restent des utilitaires d'implémentation, pas une abstraction de payload métier big-endian.

Pour tout type non plat, le parseur tente récursivement toute séquence `0x5A` dont l'en-tête, la taille et le `block_type` sont plausibles dans la borne du parent. Les types à payload fixe suivants sont toujours conservés à plat, même si leurs données contiennent un faux en-tête plausible :

```text
0x0002  0x1028  0x104f  0x204d  0x260a  0x262f  0x2637
```

Cette liste est une règle de sécurité : promouvoir un timestamp, un gain ou un pointeur contenant fortuitement `0x5A` en faux enfant peut amputer les données ou fausser les relocalisations.

### 1.4 Métadonnées temporelles et `TimecodeEngine`

- `0x1028` : exactement une racine est requise. Son premier segment brut doit mesurer au moins 6 octets; la fréquence d'échantillonnage est le UInt32 à `+2` et doit être positive.
- `0x204d` : exactement une racine est requise. Son premier octet est l'enum de cadence.
- `get_frame_rate()` expose les mappings convertibles : `0x01 = 24 fps`, `0x09 = 24000/1001 non-drop` et `0x05 = 30000/1001 Drop Frame` avec cadence nominale de 30 images.
- Une cadence inconnue peut être chargée et préservée sans conversion, mais toute méthode demandant un calcul temporel la rejette.
- `samples_to_timecode()` accepte un entier non négatif, arrondit à l'image la plus proche avec `floor(x + 0.5)` et retourne toujours `HH:MM:SS:FF`, y compris en Drop Frame.
- `timecode_to_samples()` valide les composants et interdit en 29.97 DF les labels `00` et `01` au début de chaque minute non divisible par dix.
- `duration_to_samples()` applique la cadence réelle mais n'applique pas l'interdiction des labels Drop Frame, puisqu'il convertit une durée et non une position.
- Les opérations de timeline prennent des images entières; l'API ne possède pas de paramètre de subframe. Les trims exprimés en échantillons restent, eux, sample-accurate.

## 2. Pointeurs, relocalisation et sauvegarde

### 2.1 Bloc spécial `0x0001`

Le premier bloc commence obligatoirement à l'offset `0x14` :

```text
5A | block_type=0x0001 | block_size=4 | pointeur UInt32 vers 0x0002
```

Il ne possède pas de `content_type` physique. Le parseur générique place néanmoins les deux octets bas du pointeur dans `PTBlock.content_type` et les deux octets hauts dans `items[0]`. Cette pseudo-valeur peut collisionner avec n'importe quel vrai `content_type`; toutes les recherches de racines métier excluent donc explicitement ce premier bloc.

Le bloc doit mesurer exactement 11 octets, rester le premier bloc et pointer vers l'offset absolu de l'unique `0x0002` final. Après sauvegarde, sa représentation en mémoire est resynchronisée avec les quatre octets réellement écrits.

### 2.2 Bloc final `0x0002`

`0x0002` est une racine plate, unique, finale et terminée exactement à la fin du fichier. Ses segments `bytes`/`bytearray` sont concaténés sans perte.

Un enregistrement standard mesure 15 octets :

```text
00 00 00 01 04 00 01 00 | cible UInt32 | 00 00 00
```

- Le préfixe de huit octets et le suffixe nul de trois octets sont exacts.
- Les enregistrements consécutifs forment une série. Les deux octets immédiatement avant la série contiennent son nombre d'enregistrements en UInt16 big-endian.
- Le compteur doit être présent et exactement égal à la longueur de la série.
- Chaque cible standard doit être l'`original_offset` d'un bloc réellement parsé.
- Les octets entre les séries sont des métadonnées indépendantes et sont toujours conservés.
- Ces métadonnées peuvent contenir d'autres offsets absolus. Lors de la sauvegarde, le code examine chaque alignement possible de quatre octets hors des enregistrements standards et ne remplace que les valeurs présentes dans la table `ancien_offset → nouvel_offset`. À l'intérieur d'un enregistrement, seul le champ à `+8` est admissible.
- Deux patchs de quatre octets ne peuvent pas se chevaucher.

Lorsqu'un bloc est supprimé, tous ses offsets et ceux de ses descendants sont placés dans `_removed_offsets`. La purge retire uniquement les 15 octets de chaque enregistrement standard correspondant et décrémente le compteur UInt16 BE de sa série; elle ne supprime jamais les métadonnées voisines.

### 2.3 Pipeline de `save()`

La sauvegarde est transactionnelle en mémoire et atomique sur disque :

1. Sérialiser virtuellement toutes les racines pour produire la table globale des anciens et nouveaux offsets.
2. Revalider `0x0001`, l'unique `0x0002` final, son format plat, ses compteurs et les métadonnées temporelles.
3. Purger les enregistrements des blocs réellement supprimés.
4. Relocaliser les pointeurs standards et secondaires de `0x0002`.
5. Sérialiser de nouveau, calculer l'offset final de `0x0002` et patcher `0x0001`.
6. Préserver l'en-tête déchiffré, rechiffrer une copie dans un fichier temporaire, rafraîchir tous les `original_offset`, puis remplacer atomiquement la destination.
7. Après succès, mettre à jour `self.data` avec les octets déchiffrés, vider `_removed_offsets` et stocker le chemin absolu de sortie dans `file_path`. `save()` retourne `None`.

Toute exception antérieure au remplacement restaure `root_items`, `_removed_offsets` et `file_path`. Pour les sessions prises en charge, un chargement suivi d'une sauvegarde sans mutation est byte-for-byte identique.

## 3. Catalogue des blocs compris par l'API

Les `block_type` ci-dessous sont ceux observés dans les sessions de référence. À l'exception de `0x0001`, le code métier sélectionne généralement les blocs par `content_type` et valide leur disposition, pas leur `block_type` historique.

| `content_type` | `block_type` observé | Rôle pris en charge |
|---|---:|---|
| spécial `0x0001` | `0x01` | Pointeur initial vers `0x0002`; aucun `content_type` physique. |
| `0x0002` | `0x02` | Table finale des pointeurs. |
| `0x1004` | `0x03` | Racine des liens/noms de fichiers physiques. |
| `0x1028` | `0x0a` | Fréquence d'échantillonnage. |
| `0x103a` | `0x01` | Entrée de nom ou chemin sous `0x1004`. |
| `0x104f` | `0x0a` | Payload fixe d'un événement audio ou fondu. |
| `0x1050` | `0x03` | Conteneur d'événement de timeline. |
| `0x1052` | `0x03` | Playlist visible ou cachée. |
| `0x1054` | `0x02` | Map comptée de playlists. |
| `0x2030` | `0x05` | Conteneur générique; une disposition précise représente la règle des marqueurs. |
| `0x204d` | `0x05` | Enum de cadence d'image. |
| `0x2077` | `0x12` | Définition d'un marqueur. |
| `0x2423` | `0x04` | Entrée d'index de nom de Clip Group. |
| `0x2424` | `0x01` | Liste comptée des `0x2423`. |
| `0x2425` | `0x02` | Métadonnée parallèle d'un Clip Group. |
| `0x2426` | `0x01` | Liste comptée des `0x2425`. |
| `0x2428` | `0x01` | Timeline cachée des composants d'un Clip Group. |
| `0x2506` | `0x03` | Sous-bloc du modèle de marqueur créé par l'API. |
| `0x2523` | `0x09` | Métadonnée observée dans une définition de groupe; conservée, mais non utilisée par le dégroupage. |
| `0x260a` | `0x01` | Playlist plate d'automation de volume. |
| `0x260d` | `0x05` | Conteneur des playlists d'automation d'une piste. |
| `0x2619` | `0x09` | Nom interne de piste. |
| `0x261c` | `0x04` | Définition de piste et automation. |
| `0x2628` | `0x04` | Nom et attributs d'un clip ou groupe. |
| `0x2629` | `0x0b` | Définition de clip audio. |
| `0x262a` | `0x01` | Liste globale comptée des clips audio. |
| `0x262b` | `0x01` | Définition de Clip Group. |
| `0x262c` | `0x01` | Liste globale comptée des Clip Groups. |
| `0x262f` | `0x02` | Géométrie plate de fondu. |
| `0x2630` | `0x01` | Liste globale comptée des géométries. |
| `0x2637` | `0x01` | Dictionnaire plat des points de Clip Gain. |
| `0x4826` | `0x01` | Sous-bloc du modèle de marqueur; deux occurrences. |
| `0x4827` | `0x01` | Sous-bloc du modèle de marqueur. |

## 4. Pistes et événements de timeline

### 4.1 Map `0x1054` et playlist `0x1052`

La timeline principale est l'unique racine `0x1054`. L'absence de cette racine produit une liste de pistes vide; plusieurs racines sont ambiguës.

- Le premier segment de `0x1054` commence par le nombre de `0x1052` directs en UInt32 LE.
- Le premier segment de chaque `0x1052` est `[name_len UInt32][name UTF-8][event_count UInt32]`.
- Le nom visible doit être UTF-8, non vide, et le compteur doit égaler le nombre de `0x1050` directement enfants.
- Les segments bruts et trailers non interprétés conservent leur position.

`get_tracks()` retourne uniquement les noms visibles de ces playlists, après validation complète de la map et de tous ses compteurs. Sans racine `0x1054`, il retourne `[]`.

### 4.2 Événement `0x1050 → 0x104f`

Chaque événement pris en charge contient exactement un `0x104f` brut d'au moins 16 octets. `duplicate_clip()` et les deux trims exigent le format vérifié de 35 octets; les événements Fade créés par l'API mesurent aussi 35 octets.

| Offset `0x104f` | Taille | Signification |
|---|---:|---|
| `+0` | 1 | Mute statique : `0x00` actif, `0x01` muté. |
| `+2` | 4 | ID ordinal. Pour l'audio, index dans `0x262a`; pour un fondu, index dans `0x2630`. |
| `+7` | 8 | Timestamp absolu en échantillons, UInt64 LE. |
| `+15` | 1 | Type : `0x03` audio/macro, `0x01` fondu. |
| `+33` | 1 | Liaison native utilisée par le fondu et le fragment droit d'un crossfade. |

Le payload secondaire brut direct de `0x1050` distingue les namespaces :

| Type | `block_type` du `0x104f` créé | Queue secondaire |
|---|---:|---|
| Placement audio | observé/recopié | `00 01 01`, ou la variante observée `01 01 01` lorsque `0x104f[15] == 0x03` |
| Macro Clip Group | observé/recopié | `00 00 01` |
| Fondu | `0x0a` | `01 01 01` |

Une macro de groupe n'est jamais un clip audio, même si son ID numérique est égal à un ID de `0x262a`. Les événements d'autres types sont préservés mais ne sont pas retournés par `get_timeline_clips()`.

`get_timeline_clips(include_fades=True)` retourne les événements audio et fondus triés par `(start_samples, track)`. La durée audio et le `src_offset` viennent de `0x2628`; la durée et le début d'un fondu viennent de sa géométrie. Les macros de groupes sont exclues. Sans racine `0x262a`, il retourne immédiatement `[]`.

Avec `include_fades=False`, le lecteur retourne uniquement les placements audio et ne valide pas `0x2630`, les géométries `0x262f` ni leurs liaisons aux événements Fade. Ce mode sert aux consommateurs qui n'ont besoin que de l'audio dans une session contenant des géométries de fondu encore inconnues; il ne rend pas ces géométries éditables. Le paramètre doit être un booléen.

## 5. Dictionnaires de clips et résolution des fichiers physiques

### 5.1 Liste audio `0x262a`

Lorsqu'une racine `0x262a` existe, son premier segment brut commence par le nombre de `0x2629` directs en UInt32 LE. `get_clips()` autorise son absence mais refuse plusieurs racines; les opérations ciblant un clip en exigent exactement une. L'ID d'un clip est strictement son ordinal zéro-based dans cette liste. Chaque `0x2629` pris en charge contient exactement un `0x2628` brut.

`get_clips()` valide séparément `0x262a` et `0x262c`, puis retourne les clips audio (`parent`/`virtual`) et les groupes (`group`) avec longueur et décalage convertis en timecode. L'absence des deux listes produit `[]`.

Le payload `0x2628` commence par :

```text
name_len UInt32 LE | name UTF-8 | flags UInt16 LE | queue dépendante du flag
```

Il n'existe aucun padding d'alignement après le nom. Ajouter un octet pour aligner une chaîne impaire décale immédiatement la queue et les blocs suivants.

Soit `A = 4 + name_len`, l'offset des flags :

| Flags | Type retourné | Offset source | Offset de la longueur |
|---|---|---|---|
| `0x0000`, `0x0001` | bit faible `0` : `parent`; bit faible `1` : `virtual` | absent, donc `src_offset = 0` | `A+5` |
| `0x2000`, `0x2001` | même règle du bit faible | UInt16 à `A+5` | `A+7` |
| `0x3000`, `0x3001` | même règle du bit faible | UInt24 à `A+5` | `A+8` |
| `0x4001` | `virtual` | UInt32 à `A+5` | `A+9` |

Le sélecteur indépendant à `A+2` donne la largeur de la longueur : `0x10` = UInt8, `0x20` = UInt16, `0x30` = UInt24 et `0x40` = UInt32. Cette règle s'applique à toutes les familles ci-dessus. Les sessions de production OttoAlign2 ont confirmé les variantes parent `0x2000`/`0x3000`, les sélecteurs compacts `0x10`/`0x20`, ainsi que le passage de `0x3001` à `0x4001` sur une source de référence d'environ 42 minutes.

Les trois premiers octets doivent être distingués sans ambiguïté : `01 30 40` signifie flag UInt16 `0x3001`, puis sélecteur de longueur UInt32 `0x40`; `01 40 30` signifie le véritable flag UInt16 `0x4001`, puis sélecteur de longueur UInt24 `0x30`. Tout autre flag audio — notamment `0x4000`, non observé — ou sélecteur est rejeté. La valeur maximale d'un champ UInt8 est `255`, celle d'un UInt16 `65 535`, celle d'un UInt24 `16 777 215` et celle d'un UInt32 `4 294 967 295`.

Le lecteur accepte toute combinaison observée de la matrice ci-dessus. Le writer demeure volontairement plus restreint : il ne produit que les layouts explicitement décrits dans les sections Split et Trimming/sous-clips.

Une définition clonable contient aussi un segment d'identité brut de 48 octets dans `0x2629` :

- Clip ID UInt32 à `+0`.
- UUID RFC 4122 aux octets `+22..+37`.
- `create_subclip()` exige exactement une occurrence de ce segment. `split_clip()` parcourt les segments mutables de 48 octets et remplace l'UUID de chacun; les fixtures vérifiées en contiennent exactement un, mais le code du split ne fait pas lui-même ce contrôle d'unicité.

L'index de Clip Gain signé Int32 se trouve dans les quatre octets commençant à `len(payload_2628) - 6`.

### 5.2 Définition de groupe `0x262c → 0x262b → 0x2628`

Le flag vérifié d'un Clip Group est `0x5000`. Sa longueur totale est une valeur 24-bit à `A+10`; les champs observés à `A+13` et `A+17` sont temporels, mais le lecteur public ne les consomme pas. Un groupe est retourné avec `type="group"` et `src_offset=0`.

### 5.3 `physical_filename`

Le layout standard possède un lien physique ordinal exact : le segment brut direct de 104 octets d'un `0x2629` stocke à `+96` un index UInt32 little-endian dans les enfants directs `0x1003` de l'unique catalogue racine `0x1004`. Le premier segment de `0x1004` contient leur compteur UInt32; le premier segment de chaque `0x1003` contient son ordinal one-based. La session OttoAlign2 contient des valeurs jusqu'à 176 et confirme que les quatre octets `+96..+99` appartiennent au champ; le writer n'impose donc aucune limite artificielle à 255.

Le riche enfant direct `0x103a` correspondant commence par deux compteurs, le marqueur `0x01`, le nom UTF-8 `Audio Files`, quatre octets opaques, puis un enregistrement ordonné par `0x1003` :

```text
02 00 00 00 00 | name_len UInt32 | filename UTF-8 | 45 56 41 57 ("EVAW")
```

La liste est suivie d'une queue hiérarchique. Elle ne représente pas un chemin de système de fichiers et ses libellés ne doivent jamais servir à construire le chemin du WAV :

```text
00 FF FF FF FF
pour chaque nœud i, i = 1..K :
    label_len UInt32 | label UTF-8 | opaque 4 octets | 01 | node_count_i UInt32
puis :
    terminal_label_len UInt32 | terminal_label UTF-8 | 00 00 00 00
```

`K` doit être au moins 1. Pour `N` fichiers, `node_count_i = N+i`; les deux compteurs initiaux valent respectivement `N+K+2` et `N+K+1`. Le writer valide toute la queue jusqu'au libellé terminal et incrémente les deux compteurs initiaux ainsi que chacun des `K` compteurs de nœud quand il ajoute un fichier.

La session comparative minimale possède `K=1`, le libellé `SHARE TO NETWORK`, l'opaque `46 22 18 95` et les relations `N+3`/`N+2`/`N+1`. Le diagnostic Pro Tools a confirmé qu'ajouter un fichier sans incrémenter ce troisième compteur laisse la piste visible dans la fenêtre d'import, mais bloque Pro Tools au chargement du catalogue média. Le diagnostic avec le seul changement `2→3`, puis la sortie complète `relink_API_v6.ptx` avec son WAV généré, se sont tous deux ouverts correctement.

La session de production OttoAlign2 possède `K=3` et les libellés internes `VIDEO`, `Exports` et `test ottoalign`; avec ses 177 fichiers, ses compteurs de nœud valent `178`, `179`, `180` et ses compteurs initiaux `182`, `181`. Ce sont des métadonnées PTX opaques, pas les dossiers `VIDEO/Exports` du poste courant. Le dossier physique conventionnel reste le dossier frère `Audio Files` de la session; `relink_clip()` reçoit toutefois les chemins source et destination explicitement et ne les déduit pas de ces libellés internes.

Lorsque cette structure est unique, ses compteurs sont cohérents, son nombre de noms égale le nombre de `0x1003` et le `0x2629` possède exactement un segment de 104 octets, `get_timeline_clips()` retourne directement le nom à l'index `+96`.

Les layouts non standard restent lisibles par le repli nominal best-effort :

1. Le dossier frère `Audio Files` est listé pour `.wav`, `.aif` et `.aiff`, trié sans tenir compte de la casse; ses `OSError` sont absorbées.
2. Les `0x103a` sont aussi parcourus comme texte `mac_roman` afin d'en extraire des candidats audio dédupliqués.
3. Le nom virtuel perd un suffixe final `-<chiffres>` avec extension optionnelle, puis `.A1` à `.A9`.
4. Un nom de base exact unique est préféré, puis un préfixe compatible unique.
5. Zéro ou plusieurs correspondances donnent `physical_filename = None`; aucun nom n'est inventé.

Le lecteur exact n'interprète pas encore le layout alternatif de 96 octets observé sur une définition de production; cette définition utilise le repli nominal. Le relink décrit en 6.4 exige le layout standard de 104 octets.

## 6. Mutations des clips

Toutes les recherches utilisent les noms exacts. Une définition de clip dupliquée par nom ou une cible de piste/placement non unique est rejetée avant mutation.

### 6.1 Renommage, mute, déplacement et duplication

- `rename_clip()` remplace uniquement `[name_len][name]` dans l'unique `0x2628` cible, conserve toute la queue binaire, refuse les noms vides/NUL/non UTF-8 et les collisions. Tous les placements de cette définition voient donc le nouveau nom; le retour de succès vaut `1`.
- `mute_clip()` modifie `0x104f+0` sur tous les placements audio visibles de la définition cible. Les fondus, macros de groupe et timelines cachées ne sont jamais ciblés; le retour est le nombre de placements modifiés.
- `move_clip()` exige exactement un placement visible et aucune géométrie de fondu associée. Il remplace le UInt64 à `+7`, puis retrie uniquement les slots `0x1050` de la playlist en conservant les segments bruts à leur position; le retour de succès vaut `1`.
- `duplicate_clip()` exige exactement un placement de 35 octets, sans fondu associé et avec `+33 == 0`. Il clone le `0x1050`, efface ses offsets, conserve le même ID de définition, applique le nouveau timestamp et le mute demandé, insère chronologiquement l'événement et incrémente le compteur `0x1052`. L'opération est transactionnelle, ne clone aucune géométrie de fondu et retourne l'ID de définition partagé avec la source.

### 6.2 Split

`split_clip()` est transactionnel et n'accepte que les sources vérifiées `00 00 30 44 00`, `00 00 40 44 00` ou `01 00 30 44 00` aux cinq octets commençant à `A`. La longueur source est un UInt32 à `A+5`. Pour construire le fragment gauche natif, le code recopie les triplets temporels source `A+10..A+12` et `A+14..A+16`, puis leur ajoute respectivement `00` et `FF`.

Préconditions supplémentaires :

- Une seule définition portant le nom demandé.
- Une seule piste visible portant le nom demandé.
- Un seul placement audio de cette définition qui contient strictement le cut (`start < cut < end`).
- Aucun fondu associé.
- La coupe relative, qui devient à la fois la longueur gauche et l'offset source droit, tient sur UInt24.
- La longueur droite tient sur UInt32; `0x30` est utilisé jusqu'à `0xFFFFFF`, puis `0x40` au-delà.
- Le timestamp du cut stocké dans le `0x2628` droit tient sur UInt32.

Résultat :

1. Les prochains suffixes numériques disponibles sont calculés à partir du plus grand suffixe `clip-<nombre>` existant; par exemple, `-03`/`-04` suivent `-01`/`-02`.
2. Le fragment gauche utilise `01 00 30 44 08`, `src_offset=0` et une longueur UInt32 égale à la coupe relative.
3. Le fragment droit utilise le flag `0x3001` et un offset source UInt24 égal à la coupe relative. Sa longueur restante est UInt24 sous le sélecteur `0x30`, ou UInt32 sous le sélecteur `0x40`. Le flag `0x4001` est connu grâce au trim tardif, mais le split ne l'emploie pas : un split au-delà de la limite UInt24 exigerait également un layout de fragment gauche long qui n'a pas encore été observé.
4. Les deux `0x2629` sont clonés et les segments d'identité mutables de 48 octets reçoivent de nouveaux UUID; ils sont ajoutés en fin des définitions `0x262a` et son compteur augmente de deux.
5. L'événement original est muté en fragment gauche afin de conserver son offset et son pointeur `0x0002`. Un nouvel événement droit est inséré immédiatement après; le compteur `0x1052` augmente d'un.
6. La méthode retourne `(orig_clip_id, left_clip_id, right_clip_id, cut_samples)`.

### 6.3 Sous-clips et trims

`create_subclip()` crée uniquement une définition dans le Clip Bin; il ne crée pas de placement.

- La source est un ID ordinal existant.
- Un seul `0x2628` et un seul segment d'identité de 48 octets sont requis.
- Le nom UTF-8 doit être unique.
- `src_offset >= 0`, `length > 0` et les deux valeurs tiennent chacune sur UInt32.
- Seules les combinaisons observées sont produites : offset nul/longueur UInt24; offset UInt24/longueur UInt24 ou UInt32; offset UInt32/longueur UInt24.
- Un offset nul avec une longueur supérieure à `0xFFFFFF`, ou un offset et une longueur tous deux supérieurs à `0xFFFFFF`, est rejeté faute de référence Pro Tools.
- `src_offset == 0` produit `0x0001`; un offset UInt24 positif produit `0x3001`; un offset UInt32 supérieur à `0xFFFFFF` produit `0x4001`.
- Le nouveau Clip ID et un nouvel UUID sont écrits, le bloc est ajouté après le dernier `0x2629`, puis le compteur augmente.
- Le retour est le nouvel ID ordinal.

Queues exactes produites après `[name_len][name]` :

```text
src_offset == 0:
01 00 30 44 08 | length UInt32 (valeur bornée à 24 bits)
| 00×8 | FF×7 FE
| FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

0 < src_offset <= 0xFFFFFF et length <= 0xFFFFFF:
01 30 30 44 08 | src_offset UInt24 | length UInt24
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

0 < src_offset <= 0xFFFFFF et length > 0xFFFFFF:
01 30 40 44 08 | src_offset UInt24 | length UInt32
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00

src_offset > 0xFFFFFF et length <= 0xFFFFFF:
01 40 30 44 08 | src_offset UInt32 | length UInt24
| 00×8 | FF×8
| FE FF 00 00 00 00 FF FF 04 00 04 00 | 00×21 | FF FF FF FF 00 00
```

Les deux champs `00×8` sont les deux timestamps UInt32, initialisés à zéro par un appel direct à `create_subclip()`. Le split utilise les modèles `01 30 30` ou `01 30 40` pour son fragment droit et remplace ces deux timestamps par le cut absolu. Son modèle gauche utilise `01 00 30 44 08`, écrit une longueur UInt32 et recopie les deux triplets temporels de la source selon la disposition spécialisée décrite en 6.2, avec les octets terminaux `00` puis `FF`.

`trim_clip_start()` et `trim_clip_end()` exigent exactement un placement audio visible de 35 octets, aucun fondu associé et `+33 == 0`. Ils créent une nouvelle définition via `create_subclip()`, puis relient uniquement ce placement au nouvel ID.

- Trim Start : ajoute le montant à `src_offset`, soustrait le montant de la longueur et avance le timestamp `0x104f` du même nombre d'échantillons. Il remplace aussi les deux timestamps UInt32 de la nouvelle définition `0x2628` par ce nouveau départ absolu; cette valeur doit donc tenir sur UInt32.
- Trim End : conserve `src_offset` et le timestamp, puis réduit uniquement la longueur.
- Le montant doit être un entier strictement positif et inférieur à la longueur.
- Les noms générés sont `-tS` ou `-tE`, puis `-02`, `-03`, etc. en cas de collision. Un trim composé cible le nom généré par le trim précédent.
- Toute erreur restaure l'arbre et `_removed_offsets`.
- Le retour est l'ID de la nouvelle définition créée.

### 6.4 Relink physique d'un placement

`relink_clip(track_name, clip_name, placement_start_samples, new_clip_name, source_audio_path, new_audio_path, replacement_audio_path=None)` reproduit le cas comparatif Pro Tools où deux placements partagent d'abord un WAV, puis l'un d'eux référence une identité physique indépendante. Le ciblage exige une définition de nom unique et exactement un événement audio sur la piste demandée dont le timestamp UInt64 égale `placement_start_samples`; la valeur doit aussi tenir sur UInt32 pour les champs temporels des layouts relink vérifiés. Le dernier argument facultatif fournit un WAV rendu dont seul le chunk PCM `data` sera installé dans le clone.

Préconditions binaires :

- un unique `0x1004`, dont le compteur égale ses `0x1003` directs;
- un unique `0x103a` ordonné suivant le format de 5.3, avec exactement un nom par `0x1003`, au moins un nœud de queue, les compteurs de nœud consécutifs `N+1..N+K` et les deux compteurs initiaux égaux à `N+K+2` et `N+K+1`;
- des ordinaux `0x1003` one-based consécutifs;
- un enregistrement fixe de lien média de 104 octets dans le `0x2629` source, avec index UInt32 little-endian à `+96`, et un enregistrement fixe d'identité de 48 octets. Le parseur générique peut avoir découpé un `5A xx 00 00 00 00 00` fortuit comme bloc vide : `_validated_2629_fixed_records()` resérialise alors tous les items situés entre `0x2628` et `0x4403`, puis après `0x4403`, et exige exactement 48 puis 104 octets;
- soit le layout parent/racine exact `00 00 30 44 00`, avec deux références temporelles UInt32 identiques, soit un layout virtuel de production `0x0001`/`0x2001`/`0x3001`/`0x4001` dont le marqueur vaut `high_nibble|0x04`, suivi de `0x08` et d'un sélecteur de longueur vérifié. Dans ce second cas, la référence UInt32 après l'offset et la longueur doit être supérieure ou égale à l'offset source; leur différence est la référence temporelle du média;
- dans le `0x1003` modèle, un `0x1001` brut de 31 octets et un `0x2106` possédant une queue de 58 octets ainsi qu'un en-tête vérifié de 151 octets (`time_reference+100`, second FILETIME `+105`, UUID `+135`) ou de 142 octets (`+91`, `+96`, `+126`);
- un fichier RIFF/WAVE little-endian possédant exactement un chunk `bext` d'au moins 412 octets, `minf` d'au moins 16, `regn` d'au moins 92 et `umid` d'au moins 24;
- une référence temporelle UInt64 à `bext+338` égale soit aux deux UInt64 à `regn+44`/`regn+52`, soit au second lorsque le premier vaut zéro dans le layout de production. Pour une racine, elle égale la paire du `0x2628`; pour un virtuel, elle égale la référence incorporée moins l'offset source. Lorsque le stem `regn` se termine avant `+76`, les deux tokens UInt64 à `+76`/`+84` doivent être identiques; le layout de production à stem long ne possède pas ces champs exploitables.

Le basename source doit correspondre au nom indexé par le PTX. Si le stem UTF-8 incorporé dans `regn` correspond au stem physique, il est remplacé; une abréviation de production divergente, par exemple `Rdy` contre `Ready`, est préservée sans être interprétée comme une erreur. Le nouveau nom physique doit être absent du catalogue, le chemin de destination absent et son dossier existant. Dans l'usage normal, l'appelant construit les deux chemins à partir du dossier `Audio Files` frère du fichier PTX; les libellés de la queue `0x103a` n'interviennent jamais dans cette résolution. Le stem UTF-8 du nouveau WAV doit différer du stem source tout en ayant exactement la même longueur, afin de rester compatible avec les champs fixes de l'identité physique.

Mutation PTX :

1. Clone le `0x1003` source, efface tous ses offsets, lui attribue l'ordinal suivant et synchronise ses identifiants `0x1001`/`0x2106` avec le nouvel UMID du WAV. Les offsets de référence, second FILETIME et UUID dépendent de la taille 151/142 donnée ci-dessus. Le FILETIME à `+29` est arrondi vers le bas à la seconde et le second vaut exactement une seconde de moins; tous deux ont donc un reste nul modulo `10 000 000`.
2. Incrémente les deux compteurs d'en-tête et tous les compteurs de nœud de la queue `0x103a`, y insère un enregistrement `EVAW`, incrémente le compteur `0x1004` et place le clone après le dernier `0x1003`.
3. Clone le `0x2629`, normalise ses deux enregistrements fixes, génère un nouvel ID ordinal à `identity+0` et un UUID aux octets `identity+23..+38` — l'octet sentinelle `+22` est préservé —, remplace le nom `0x2628`, puis écrit le nouvel index physique UInt32 à `media_link+96`. Une racine reçoit `placement_start_samples` dans sa paire temporelle; un virtuel conserve intégralement sa queue et sa référence incorporée.
4. Incrémente `0x262a`, insère le clone après la dernière définition et remplace uniquement l'ID UInt32 à `0x104f+2` de l'événement ciblé.
5. Aucun enregistrement `0x0002` n'est ajouté : les neuf nouveaux blocs ont des offsets neufs et la référence Pro Tools comparative conserve une table `0x0002` de taille identique.

Mutation WAV :

- le fichier complet est copié vers un temporaire dans le dossier de destination; par défaut, le chunk PCM `data` reste identique;
- dans la référence d'origine de 32 octets de `bext`, seuls les quatre octets ASCII `+294..+297` (`bext+288+6..9`) sont renouvelés, conformément à la comparaison native; le reste est préservé. `bext` reçoit aussi la date/heure locale, une référence temporelle égale au placement ciblé et le nouvel UMID; `minf` reçoit le FILETIME exact non arrondi;
- `regn` préserve son mode de références : `(new,new)` dans le layout court et `(0,new)` dans le layout de production. Les deux tokens opaques sont renouvelés seulement lorsqu'ils existent avant le stem; le stem est remplacé seulement lorsqu'il concordait avec le nom physique. `regn` et `umid` reçoivent l'identifiant compact concordant;
- si `replacement_audio_path` est fourni, son RIFF doit posséder un unique `fmt ` d'au moins 16 octets et un unique `data`. Seuls PCM `0x0001` et WAVE_EXTENSIBLE `0xFFFE` avec sous-format PCM `1` sont admis. Canaux, fréquence, byte rate, block align, bits par échantillon et taille exacte de `data` doivent égaler le clone; les octets PCM sont copiés par blocs de 1 Mio sans remplacer les autres chunks;
- le remplacement final est atomique et ne peut écraser un fichier existant.

La mutation de l'arbre PTX est transactionnelle. Une erreur avant le remplacement final supprime le temporaire et restaure l'arbre. Après succès, le WAV existe déjà mais la session n'est encore qu'en mémoire : l'appelant doit exécuter `save()` et nettoyer le WAV lui-même si cette sauvegarde ultérieure échoue. Le retour est un dictionnaire contenant le nouvel ID de clip, son nom, l'index physique et le nom du WAV.

## 7. Fondus et crossfades

### 7.1 Dictionnaire `0x2630`

Une opération de fondu exige exactement une racine `0x2630`. Son premier segment commence par un compteur UInt32 égal au nombre de `0x262f` directs, chacun possédant un payload brut.

Chaque événement Fade (`0x104f[15] = 0x01`) utilise son UInt32 à `+2` comme index ordinal de `0x262f`. Le nombre d'événements Fade doit égaler le nombre de géométries; chaque index doit être valide et utilisé une seule fois.

| Géométrie | Taille | Champs lus/écrits |
|---|---:|---|
| Fade In | 22 | Discriminateur `0x20` à `+5`; longueur UInt16 à `+8`; mode créé `0x03` à `+10`; forme à `+11`. L'ancre de l'événement est le début du fade et doit correspondre au début d'un placement audio unique. |
| Fade Out | 26 ou 27 en lecture; 27 en création | Discriminateur créé `0x22` à `+5`; longueur UInt16 à `+8`; la création la répète à `+10`, écrit le mode `0x02` à `+12` et la forme à `+13`. L'ancre est la fin du fade et doit correspondre à la fin d'un placement unique. |
| Crossfade | 34 | Discriminateur créé `0x22` à `+5`; pré-roll UInt16 à `+8`; durée totale UInt16 à `+10`; mode `0x01` à `+12`; Equal Power `0x01` à `+13`. L'ancre est le cut. |

Une autre longueur de payload est rejetée. Pour la lecture d'un crossfade, l'audio droit commençant à l'ancre est préféré; l'audio gauche finissant à l'ancre sert de repli. L'association doit rester unique.

### 7.2 Fade autonome

`add_fade()` prend une ancre absolue et une durée. Une durée convertie à zéro sélectionne une seconde (`sample_rate` échantillons). La durée finale doit tenir sur UInt16, donc être au plus `65 535` échantillons.

- Types : `in` et `out`, sans tenir compte de la casse.
- Formes : `power = 0x01` et `linear = 0x02`.
- Fade In : l'ancre peut être au début du placement mais pas à sa fin; le fade s'étend vers la droite et doit rester dans le clip.
- Fade Out : l'ancre peut être à la fin du placement mais pas à son début; le fade s'étend vers la gauche et doit rester dans le clip.
- Le chemin d'écriture accepte techniquement une ancre intérieure. Le lecteur `get_timeline_clips()` ne peut toutefois réassocier un Fade In que si son ancre égale le début du placement, et un Fade Out que si elle égale sa fin. Pour une session relisible par toute l'API, les fades autonomes doivent donc rester des fades de bord.
- La combinaison piste, définition et ancre doit résoudre un seul placement.
- Un fondu équivalent déjà lié à la même piste/ancre est refusé.
- La nouvelle géométrie est ajoutée à la fin des géométries, tandis que le `0x1050` est inséré chronologiquement : avant un audio au même timestamp pour un Fade In, après pour un Fade Out.
- Le `0x104f` créé mesure 35 octets, reste actif à `+0`, référence le nouvel index de géométrie à `+2` et n'active pas le lien crossfade à `+33`.
- L'opération est transactionnelle.
- Le retour est l'index ordinal de la nouvelle géométrie.

Pour tous les fondus créés, les octets `0x104f[15:35]` partent du modèle `01 FE FF 00 00 00 00 FF FF FF FF FF FF FF FF 00 00 00 00 00`; le crossfade remplace ensuite `+33` par `0x01`.

### 7.3 Crossfade centré

`add_crossfade()` convertit une durée strictement positive, au plus `65 535` échantillons, valide `0x2630`, puis appelle `split_clip()` dans une transaction englobante.

- La géométrie de 34 octets utilise `pre_roll = floor(duration / 2)` et la durée totale demandée. Pour une durée impaire, l'échantillon excédentaire se trouve donc du côté droit.
- Le mode et la courbe sont toujours Equal Power (`0x01`, `0x01`).
- Le fondu est inséré immédiatement avant le fragment droit.
- Le `+33` du fondu et celui du fragment droit passent à `0x01`; le mute du fondu reste `0x00`.
- Les limites et refus du split s'appliquent intégralement.
- Le retour normal est `None`.

Les fondus existants peuvent être lus et de nouveaux fondus peuvent être ajoutés, mais aucune API ne permet de déplacer, remodeler ou supprimer une géométrie existante. Move, duplicate, split et trims refusent les placements auxquels un fondu est attaché.

`0x2077` n'est pas un cache de fondu : il appartient aux marqueurs. Aucun `0x2077` n'est nécessaire pour le calcul audio d'un fade.

## 8. Marqueurs

`0x2030` est générique. Un bloc racine est reconnu comme règle de marqueurs seulement s'il possède l'une des deux dispositions suivantes :

- Vide : un seul segment brut de 12 octets, dont le UInt32 initial vaut zéro.
- Peuplé : un segment initial de 4 octets contenant le compteur UInt32, uniquement des `0x2077` directs, puis un trailer brut de 8 octets. Le compteur doit égaler le nombre de marqueurs.

Le premier segment brut de chaque `0x2077` lu par l'API possède :

| Offset | Taille | Champ |
|---|---:|---|
| `+0` | 2 | Index UInt16. |
| `+2` | 4 | Octets structuraux; le modèle créé utilise `03 09 00 00`. |
| `+6` | 4 | Longueur du nom UInt32. |
| `+10` | variable | Nom UTF-8. |
| `+10+N` | 8 | Premier timestamp Int64; utilisé par `get_markers()`. |
| `+18+N` | 8 | Second timestamp Int64; `add_marker()` écrit la même valeur pour créer un point. |

`get_markers()` exige des index uniques dans toutes les règles reconnues et convertit le premier timestamp en timecode. Sans règle reconnue ou sans marqueur, il retourne `[]`.

`add_marker()` exige :

- Au moins une playlist principale entièrement valide.
- Exactement une règle de marqueurs reconnue.
- Un nom `str` sans NUL, encodable en UTF-8 et dont la taille tient sur UInt32.
- Un timestamp entier entre `0` et `2^63-1`.
- Un index explicite unique entre `1` et `65 535`, ou `max(index existant)+1`.

Le marqueur créé dérive d'un modèle natif contenant `0x2506`, deux `0x4826`, un `0x4827` et des segments bruts. Le lecteur ne prétend pas que ces sous-blocs sont obligatoires dans toutes les révisions; ils décrivent seulement le modèle écrit par l'API. Un nouvel UUID RFC 4122 est injecté aux octets `23..38` du segment secondaire du `0x2077`, puis tous les offsets du modèle sont effacés avant insertion.

`add_marker()` retourne l'index effectivement écrit.

L'API crée des marqueurs ponctuels seulement; elle ne modifie/supprime pas les marqueurs existants et ne crée pas de sélection ou de propriétés avancées de Memory Location.

## 9. Clip Gain statique

`0x2637` est une racine plate unique :

```text
point_count UInt32 LE | point 0 (30 octets) | ... | point N-1
```

Chaque point contient 26 octets de métadonnées et une valeur Float32 LE aux octets `26..29`. La taille du payload doit être exactement `4 + 30 × point_count`.

Dans chaque `0x2628`, l'index signé Int32 à `len(payload)-6` vaut `-1` sans Clip Gain ou référence un point existant. Tous les index de toutes les définitions sont validés avant écriture.

`set_clip_gain()` :

- Cible une définition de clip au nom unique; toutes ses occurrences partagent donc le gain statique.
- Accepte, sauf les booléens, toute valeur convertible par `float()` vers un réel fini, ainsi que `-math.inf`, `"-inf"` ou `"-infinity"`; `NaN` et `+inf` sont rejetés.
- Remplace `-inf` par la sentinelle Pro Tools Float32 `-290.2105712890625`, octets LE `f4 1a 91 c3`.
- Si l'index vaut `-1`, ajoute un point avec les 26 octets de métadonnées `01 46 01 00 16 00 00 00 00 00 01 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00`.
- Si plusieurs définitions partagent le point, clone les 30 octets et relie uniquement la cible au nouvel index.
- Sinon, remplace uniquement les quatre octets Float32 du point existant.
- Le retour est l'index du point global finalement lié à la définition.

Séquences Float32 LE de référence :

| Valeur | Octets LE | UInt32 équivalent |
|---:|---|---:|
| `0.0` | `00 00 00 00` | `0x00000000` |
| `-10.0` | `00 00 20 c1` | `0xc1200000` |
| `+6.0` | `00 00 c0 40` | `0x40c00000` |
| sentinelle `-inf` | `f4 1a 91 c3` | `0xc3911af4` |

Les enveloppes/breakpoints de Clip Gain ne sont pas pris en charge.

## 10. Automation de volume

La cible est une définition `0x261c` :

- Chaque définition doit contenir exactement un `0x2619`; son payload est `[name_len UInt32][name UTF-8]`.
- Le nom visible validé de `0x1052` a priorité et est associé par ordinal aux `0x261c`; cette association exige le même nombre de pistes visibles et de définitions.
- Si aucun nom visible ne correspond, un nom interne `0x2619` unique sert de repli.
- La cible doit contenir exactement un `0x260d`.
- La première occurrence `0x260a` directement enfant de ce `0x260d` est la playlist de volume. Une occurrence imbriquée, notamment sous `0x260c`, ne la remplace pas.

Payload plat `0x260a` :

| Offset | Taille | Champ |
|---|---:|---|
| `+0` | 4 | Identifiant exact `01 46 01 00`. |
| `+4` | 4 | Taille déclarée = `len(payload)-10`. |
| `+8` | 2 | Padding nul. |
| `+10` | 4 | Nombre de nœuds UInt32. |
| `+14` | 8 | Flags. Le UInt32 à `+16` est le nombre de segments, soit `max(0, nodes-1)`. |
| `+22` | `6 × N` | Nœuds triés. |
| fin | 2 | Terminateur nul. |

Chaque nœud est `[timestamp UInt32][valeur Int16]`. La valeur est en déci-dB. Les timestamps existants doivent être strictement croissants et uniques; un ajout au même timestamp remplace le nœud, sinon il est inséré et la liste est retriée.

`add_volume_node()` exige un timestamp `0..2^32-1` et, sauf pour les booléens, une valeur convertible par `float()` puis finie, dont le `round()` Python de `db × 10` tient sur Int16 (`-32768..32767`). Il reconstruit la taille, le compteur de nœuds, le compteur de segments et le terminateur. Il ne supprime aucun nœud, ne touche aucune autre automation et retourne le nombre total de nœuds après écriture.

## 11. Clip Groups

La lecture des définitions est indépendante des clips audio : `0x262c` contient un compteur UInt32 et des `0x262b`; l'ID de groupe est leur ordinal. Les macros de timeline utilisent ce namespace, pas celui de `0x262a`.

La disposition prise en charge pour `delete_clip_group()` est volontairement étroite :

- Une unique racine `0x262c` contenant exactement un groupe simple.
- Une unique racine cachée `0x2428`, contenant un unique `0x1054`, lui-même contenant exactement une `0x1052`.
- La playlist cachée contient au moins un `0x1050`, et tous sont des placements audio (`type 0x03`, queue `00 01 01`); aucun groupe imbriqué, fondu ou événement non audio.
- La timeline principale possède exactement une macro (`queue 00 00 01`) dont l'ID égale l'ordinal du groupe.
- Une unique racine `0x2424` et une unique racine `0x2426`; leurs compteurs et ceux de `0x262c` sont égaux.
- Le `0x2423` correspondant contient la longueur du nom à `+4` et le nom UTF-8 à `+8`; l'entrée `0x2425` correspond par ordinal.

Le dégroupage :

1. Calcule `hidden_origin = min(timestamp des composants)`.
2. Rebase chaque composant avec `macro_start + hidden_timestamp - hidden_origin`, sous borne UInt64.
3. Déplace les blocs `0x1050` existants vers la playlist principale à la place de la macro, puis retrie uniquement les slots d'événements.
4. Met à jour le compteur principal avec `ancien - 1 + nombre_de_composants`.
5. Supprime la playlist cachée et met le compteur de son `0x1054` à zéro, tout en conservant les conteneurs `0x2428`/`0x1054` vides.
6. Retire le `0x262b`, le `0x2423` et le `0x2425`, puis décrémente leurs trois compteurs. Les racines vides `0x262c`, `0x2424` et `0x2426` sont conservées.
7. Enregistre tous les offsets supprimés afin que `save()` purge leurs enregistrements `0x0002`.

L'opération complète est transactionnelle. Sans racine `0x262c`, `delete_clip_group()` retourne `0`; une dissolution réussie retourne `1`. L'API peut lire et dissoudre ce groupe simple, mais ne crée aucun Clip Group; le lien nécessaire autour de `0x2428`/`0x2501` n'est pas suffisamment établi.

## 12. Limites fonctionnelles consolidées

- Édition de sessions PTX existantes seulement; aucune création complète de session.
- Payloads métier little-endian seulement.
- Conversions temporelles limitées à 24, 23.976 non-drop et 29.97 Drop Frame.
- Audio et fondus seulement sur la timeline. MIDI, contrôleurs continus, pistes/clips vidéo, Inserts, Sends, routing I/O, Pan, Mute automation et automation de plugins ne sont pas pris en charge.
- Aucune création/suppression/renommage/réorganisation de pistes, import/export audio général ou suppression arbitraire de définitions/événements. Le seul relink pris en charge est le clonage WAV exact décrit en 6.4.
- Pas de création de Clip Group; dissolution limitée au cas simple documenté.
- `create_subclip()` et les trims produisent les combinaisons vérifiées offset UInt24/longueur UInt32 (`01 30 40`) et offset UInt32/longueur UInt24 (`01 40 30`). Ils rejettent encore un sous-clip virtuel d'offset nul et de longueur supérieure à UInt24, ainsi que la combinaison offset UInt32/longueur UInt32, faute de référence Pro Tools. Le split accepte les racines courtes et longues vérifiées et une longueur droite UInt32, mais la coupe relative reste limitée à UInt24 tant que le layout du fragment gauche d'un split tardif n'a pas été observé.
- Move, duplicate, split et trims refusent les placements avec fondus attachés; la duplication ne clone pas les fades.
- Fades ajoutés seulement; aucune édition/suppression de fade existant. Crossfade centré Equal Power seulement.
- Marqueurs ponctuels ajoutés seulement; aucune édition/suppression, sélection ou propriété avancée.
- Clip Gain statique seulement; aucune enveloppe. Automation de piste : Volume seulement, ajout/remplacement de nœuds, sans suppression.
- Résolution exacte du fichier physique lorsque le catalogue indexé `0x1004`/`0x103a` vérifié est présent; repli nominal sans garantie dans les autres layouts. Le lecteur public ne vérifie pas l'UUID BWF, tandis que le relink renouvelle et synchronise explicitement l'identité BWF/PTX de son clone.
- Arbres limités à 128 niveaux, `block_type` pris en charge sur 8 bits, contenus/offsets sur UInt16/UInt32 et fichiers sérialisés dans l'espace UInt32.
- Les révisions PTX non présentes dans le corpus peuvent contenir des flags, géométries ou conteneurs inconnus; ils sont préservés lorsqu'ils restent opaques, mais une opération qui doit les interpréter les rejette.

## 13. Catalogue exhaustif des erreurs

La portée d'« exhaustif » est la suivante : toutes les familles d'échecs explicitement détectées ou propagées par `pt_api.py` 1.3.7, ainsi que tous les messages Pro Tools consignés dans le corpus et l'historique des essais du projet. Elle ne prétend pas recenser les messages possibles de toutes les versions de Pro Tools.

Le source courant contient 441 instructions `raise` : 373 `ValueError`, 36 `TypeError`, 8 `NotImplementedError`, 9 `OverflowError`, 5 `FileNotFoundError`, 1 `FileExistsError`, 1 `OSError` et 8 relances nues de l'exception originale.

### 13.1 Messages observés dans Pro Tools

| Message affiché | Causes techniques couvertes | Prévention/réparation |
|---|---|---|
| **Magic ID does not match** | `0x0001` pointe au mauvais offset; un enregistrement standard ou un offset secondaire de `0x0002` est obsolète; un bloc a été supprimé sans purger son pointeur; une relocalisation a utilisé un offset dupliqué. | Recalculer tous les offsets, patcher `0x0001`, relocaliser `0x0002`, purger exactement les enregistrements des blocs supprimés et refuser les `original_offset` dupliqués. |
| **Unexpected stream type** | Un faux bloc a été créé par un `0x5A` fortuit; `block_type`, taille ou `content_type` ne correspondent plus au flux attendu; l'ordre ou l'enveloppe d'un bloc a été altéré. | Garder les payloads fixes à plat, conserver les octets opaques, sérialiser l'en-tête générique dans l'ordre exact et ne générer que les dispositions vérifiées. |
| **End of stream** | Bloc/payload tronqué; taille déclarée trop grande; compteur de `0x0002`, `0x1054`, `0x1052`, `0x2030`, `0x2424`, `0x2426`, `0x262a`, `0x262c`, `0x2630`, `0x2637` ou `0x260a` supérieur aux données réelles; compteur de série `0x0002` non ajusté; table `0x0002` amputée par un faux enfant; ajout de padding d'alignement; longueur de nom décalant la queue. | Valider toutes les tailles et compteurs avant mutation/sauvegarde, ne jamais aligner artificiellement les chaînes/blocs, garder `0x0002` plat et retirer uniquement les 15 octets d'un enregistrement standard. |
| **Cannot open the selected file because end of stream encountered** | Variante d'interface du même échec **End of stream**, observée lors des premiers essais de dissolution de Clip Group et de crossfade dont la structure sérialisée était incomplète. | Appliquer les mêmes contrôles que pour **End of stream**, en particulier compteurs, padding, payloads fixes et intégrité complète de `0x0002`. |

### 13.2 Exceptions de l'API

| Exception | Conditions exhaustives par famille |
|---|---|
| `TypeError` | Chemin non path-like ou résolu en `bytes`; tampon non `bytes`/`bytearray`; enum/composants/timecode/timestamp/index non entiers; paramètres `mute`/endianness/`include_fades` non booléens; champs `PTBlock` ou `base_offset` du mauvais type; item d'arbre non `bytes`/`bytearray`/`PTBlock`; nom attendu non `str`; gain/volume non convertibles en réel; offset/longueur de sous-clip ou montant de trim non entier; type/forme de fade non `str`. |
| `ValueError` — enveloppe et temps | Chemin vide; fichier/en-tête trop court; signature/version/endianness/mode XOR invalide; delta XOR introuvable; sample rate non fini, non positif ou hors UInt32; cadence inconnue; composant/timecode/drop-frame invalide; sample négatif; conversion temporelle non représentable. |
| `ValueError` — arbre, parsing et sauvegarde | `block_type`, `content_type`, tailles ou offsets hors bornes; profondeur >128; cycle; `original_offset` dupliqué; `0x0001` absent/invalide/mal placé; `0x0002` absent/dupliqué/non final/non EOF/non plat/vide; liaison `0x0001→0x0002` fausse; record/suffixe/compteur de série `0x0002` invalide; cible de pointeur inconnue; relocalisations chevauchantes; métadonnées `0x1028`/`0x204d` absentes, dupliquées ou tronquées. |
| `ValueError` — pistes et événements | Racines `0x1054` ambiguës; compteurs `0x1054`/`0x1052` incohérents; header, nom UTF-8, compteur ou structure `0x1050→0x104f` invalide; queue audio inconnue; ID de clip timeline inconnu; piste/placement absent ou ambigu; timestamp cible hors champ de stockage. |
| `ValueError` — clips et noms | `0x262a`/`0x262c` ambigu, compteur ou définition invalide; `0x2628` tronqué, nom UTF-8 invalide, flag audio/groupe inconnu ou sélecteur de largeur autre que `0x10`/`0x20`/`0x30`/`0x40`; clip absent/ambigu; nouveau nom vide, NUL, non UTF-8, trop long ou déjà présent; ID source inconnu; modèle `0x2629` sans unique identité 48 octets; offset/longueur de sous-clip direct négatif, nul ou hors UInt32; layout offset nul/longueur UInt32 ou offset UInt32/longueur UInt32 non vérifié. |
| `ValueError` — média physique et relink | RIFF/WAVE invalide, big-endian, tronqué, de taille ou d'alignement incohérent; chunk `bext`/`minf`/`regn`/`umid` absent, dupliqué ou trop court; `fmt ` ou `data` du clone/rendu absent, dupliqué ou tronqué; rendu autre que PCM/WAVE_EXTENSIBLE PCM, format PCM incompatible ou taille `data` différente; UMID, stem complet ou abrégé, paire de tokens ou références temporelles `bext`/`regn` invalides ou non concordantes avec le PTX; basename source différent du catalogue PTX ou du stem `regn`; chemin source et destination identiques; extension autre que `.wav`; stems non UTF-8, identiques ou de longueurs UTF-8 différentes; catalogue `0x1004`/`0x103a`, compteurs, noms, ordinaux ou index média invalides/ambigus; queue `0x103a` tronquée, sans nœud parent, avec libellé vide/NUL/non UTF-8, marqueur inconnu, terminaison invalide ou compteurs non conformes à `N+1..N+K`, `N+K+2`, `N+K+1`; nouveau nom physique déjà catalogué ou trop long; enregistrements fixes `0x2629` de 48/104 octets impossibles à réassembler ou mal ordonnés; `0x1001`, modèle `0x2106` standard ou de production (142/151 octets), ou `0x2628` absent, ambigu, tronqué ou de layout inconnu; layout source autre que le parent/racine ou virtuel de production vérifié; référence virtuelle non concordante avec le BWF et l'offset source; timestamp relink hors UInt32; nouvelle définition de clip en collision; placement exact absent ou ambigu. |
| `ValueError` — opérations de montage | Aucun placement visible; plusieurs placements; cut hors du clip ou partagé par plusieurs occurrences; source de split hors layout racine vérifié; coupe/offset source relatif hors UInt24; timestamp de coupe ou nouveau timestamp de Start Trim hors UInt32; payload audio non 35 octets; montant de trim nul/négatif/trop grand; composant d'image invalide ou label Drop Frame interdit. Toute opération transactionnelle restaure l'état avant de relancer l'erreur. |
| `ValueError` — fondus | `0x2630` absent/dupliqué; compteur ou payload `0x262f` invalide; nombre d'événements et géométries différent; ID inconnu/dupliqué; taille géométrique autre que 22/26/27/34; association audio absente/ambiguë; début calculé négatif; durée nulle pour crossfade ou >UInt16; type/forme invalide; clip de durée nulle; cible hors clip/ambiguë; fade dépassant les bornes ou déjà existant. |
| `ValueError` — marqueurs | Session sans playlist principale; règle `0x2030` absente/dupliquée/mal formée; compteur incohérent; payload `0x2077`, longueur ou UTF-8 invalide; index dupliqué/hors `1..65535`; timestamp hors Int64; nom NUL/non UTF-8/trop long; modèle ou zone UUID interne invalide. |
| `ValueError` — Clip Gain | Dictionnaire `0x2637` absent/dupliqué/mal formé; compteur/taille incohérent; index de définition hors dictionnaire; gain `NaN`/`+inf` ou hors Float32; payload de clip trop court. |
| `ValueError` — Volume | Nom de piste vide/NUL/ambigu; association visible→`0x261c` impossible; `0x2619`, `0x260d` ou `0x260a` absent/ambigu/mal formé; magic, taille, padding, terminateur, compteur de nœuds ou segments incohérent; timestamps non strictement croissants; timestamp hors UInt32; valeur non finie ou hors Int16 déci-dB. |
| `ValueError` — Clip Groups | Nom vide/groupe absent; racines, compteurs, noms UTF-8 ou métadonnées `0x262c`/`0x2428`/`0x2424`/`0x2426` incohérents; playlist cachée vide/mal formée; macro ou index de nom non concordant. |
| `NotImplementedError` | Exactement huit refus explicites : session contenant plus d'un Clip Group; groupe contenant plus d'une piste; groupe imbriqué/fade/événement non audio; groupe placé zéro ou plusieurs fois au lieu d'une; move avec fade attaché; duplicate avec fade attaché; split avec fade attaché; trim avec fade attaché. |
| `OverflowError` | Offset de bloc sérialisé hors UInt32; payload `PTBlock` hors champ de taille UInt32; bloc dépassant l'espace fichier UInt32; timestamp restauré d'un groupe hors UInt64; nouvel index de point Clip Gain hors Int32 signé; nouvel ID de clip, index média de relink ou compteurs de noms physiques hors UInt32. |
| `FileNotFoundError` | Fichier d'entrée absent (propagé nativement); dossier de destination inexistant pour `xor_session()`/`save()`; WAV source ou WAV de remplacement absent, ou dossier du nouveau WAV inexistant pour `relink_clip()`. |
| `FileExistsError` | Le chemin du nouveau WAV demandé à `relink_clip()` existe déjà; aucun écrasement n'est permis. |
| `PermissionError` et autres `OSError` natifs | Erreur d'ouverture/lecture/création/remplacement du système de fichiers; elles conservent leur sous-type. La recherche facultative dans `Audio Files` est la seule exception : ses `OSError` sont absorbées et la résolution continue avec `0x1004`. |
| `OSError` explicite | Écriture chiffrée plus courte que la taille attendue. Le fichier temporaire est supprimé et la destination existante reste intacte. |
