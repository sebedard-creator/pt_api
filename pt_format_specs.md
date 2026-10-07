# Spécifications binaires du format Pro Tools (`.ptx`)

*(Spécification normative de `pt_api` 1.5.2; sessions de référence produites par Pro Tools Ultimate 2024.3.1 à 23.98, 24 et 29.97df fps, plus layouts Premiere Pro observés.)*

Ce document décrit exactement les structures que le code courant lit, valide, modifie et sérialise. Une structure dite « observée » provient des sessions de référence; une structure dite « prise en charge » possède un chemin explicite dans `pt_api.py`. Les zones non interprétées sont conservées telles quelles et ne doivent pas être déduites par heuristique.

**Provenance et licence** : ptformat (https://github.com/zamaudio/ptformat), dont la source crédite Damien Zammit et Robin Gareus, a servi de référence et d'inspiration initiale pour comprendre le format et son déchiffrement. La distribution source courante de pt_api est sous **LGPL-2.1-or-later** depuis la correction de notices du 7 octobre 2026; voir LICENSE, COPYING et NOTICE.md. Cette mention n'attribue pas chaque offset ou découverte documentée à ptformat, ne prétend pas établir une copie ligne par ligne et ne remplace pas la revue détaillée de provenance. Aucun comportement, champ binaire ou contrat d'erreur ne change avec cette correction de licence.

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

Pour tout type non plat, le parseur tente récursivement toute séquence `0x5A` dont l'en-tête, la taille et le `block_type` sont plausibles dans la borne du parent, sauf dans un préfixe brut explicitement défini. Les types à payload fixe suivants sont toujours conservés à plat, même si leurs données contiennent un faux en-tête plausible :

```text
0x0002  0x1028  0x104f  0x204d  0x260a  0x262f  0x2637
```

Cette liste est une règle de sécurité : promouvoir un timestamp, un gain ou un pointeur contenant fortuitement `0x5A` en faux enfant peut amputer les données ou fausser les relocalisations.

**Correctif local non publié — compteur de playlists** : `RAW_PREFIX_PTX_CONTENT_TYPES` définit uniquement `0x1054: 4`. Les quatre premiers octets du payload de toute track map `0x1054`, principale ou imbriquée dans une playlist de Clip Group, sont conservés comme données brutes avant toute recherche d'enfant. Ce UInt32 est le compteur de playlists : la valeur 90 (`5A 00 00 00`) ne doit jamais devenir un faux en-tête de bloc lorsqu'elle précède un `0x1052`. Aucun compteur n'est changé ou réparé automatiquement. Un préfixe tronqué est préservé par le parseur; le validateur de playlists continue à rejeter les compteurs de moins de quatre octets et les nombres incohérents. Les autres types de conteneurs ne reçoivent pas cette règle par inférence. La suite synthétique couvre 90 et 346 playlists, le nesting, les pointeurs standards et les sauvegardes. Une session native 2024.3.1 à 90 playlists Audio mono vides confirme la lecture ordonnée et deux cycles byte-identiques de sauvegarde/rechargement; elle charge aussi avant le correctif et n'est donc pas une reproduction native du bug. L'utilisateur a validé l'ouverture, les 90 pistes, la sauvegarde et la réouverture de la sortie dans Pro Tools 2024.3.1. Le fichier resauvegardé par Pro Tools charge dans l'API et reste byte-identique lors de deux nouveaux cycles API. Les deux fixtures natives font l'objet de tests optionnels locaux; elles ne sont pas distribuées avec le paquet.

### 1.4 Métadonnées temporelles et `TimecodeEngine`

- `0x1028` : exactement une racine est requise. Son premier segment brut doit mesurer au moins 6 octets; la fréquence d'échantillonnage est le UInt32 à `+2` et doit être positive.
- `0x204d` : exactement une racine est requise. Son premier octet est l'enum de cadence.
- `get_frame_rate()` expose les mappings convertibles : `0x01 = 24 fps`, `0x09 = 24000/1001 non-drop`, `0x05 = 30000/1001 Drop Frame` avec cadence nominale de 30 images et, dans le changement local non publié, `0x02 = 25 fps non-drop` avec cadence nominale de 25 images.
- Une cadence inconnue peut être chargée et préservée sans conversion, mais toute méthode demandant un calcul temporel la rejette.
- `samples_to_timecode()` accepte un entier non négatif, arrondit à l'image la plus proche avec `floor(x + 0.5)` et retourne toujours `HH:MM:SS:FF`, y compris en Drop Frame.
- `timecode_to_samples()` valide les composants et interdit en 29.97 DF les labels `00` et `01` au début de chaque minute non divisible par dix.
- `duration_to_samples()` applique la cadence réelle mais n'applique pas l'interdiction des labels Drop Frame, puisqu'il convertit une durée et non une position.
- Les opérations de timeline prennent des images entières; l'API ne possède pas de paramètre de subframe. Les trims exprimés en échantillons restent, eux, sample-accurate.

**Étape 2 non publiée — 25 fps** : une session native Pro Tools 2024.3.1 confirme `0x204d[0] == 0x02`, à 48 kHz. L'image vaut exactement 1920 échantillons. Le clip natif de 96000 échantillons débute à `1728046080` (`10:00:00:24`) et finit à `1728142080` (`10:00:02:24`); les markers natifs sont à `1728046080`, `1728048000` (`10:00:01:00`) et `1730880000` (`10:01:00:00`). `move_clip()` vers `10:00:01:00` écrit seulement le UInt64 de placement `0x104f+7`, soit +1920 échantillons, sans toucher aux définitions, médias ou markers; la nouvelle fin est `1728144000` (`10:00:03:00`). Les tests confirment les conversions position/durée, l'arrondi à la demi-image, les transitions seconde/minute, le rejet de `FF >= 25`, les calculs à 44.1/48/96 kHz, le cycle natif byte-identique et les limites d'authoring conservées. L'utilisateur confirme l'ouverture/sauvegarde/réouverture de la sortie déplacée dans Pro Tools 2024.3.1; le fichier resauvegardé conserve le clip et les markers attendus et passe deux nouveaux cycles API byte-identiques, couverts par un second test natif optionnel. Cette extension du moteur temporel n'élargit pas les profils de création stricts 48 kHz / 23.976 du builder et des groupes vides.

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

Lorsqu'un bloc est supprimé, tous ses offsets et ceux de ses descendants sont placés dans `_removed_offsets`. La purge générique retire uniquement les 15 octets de chaque enregistrement standard correspondant et décrémente le compteur UInt16 BE de sa série; elle ne supprime jamais les métadonnées voisines. `delete_tracks()` possède une exception strictement profilée documentée en §4.1.2 : trois groupes de métadonnées de piste, observés natifs, sont retirés comme unités complètes avec leurs records standards incorporés. Aucune autre métadonnée voisine n'est supprimée par inférence.

### 2.3 Pipeline de `save()`

La sauvegarde est transactionnelle en mémoire et atomique sur disque :

1. Sérialiser virtuellement toutes les racines pour produire la table globale des anciens et nouveaux offsets.
2. Revalider `0x0001`, l'unique `0x0002` final, son format plat, ses compteurs et les métadonnées temporelles.
3. Capturer l'identité des records et, pour une suppression de pistes vérifiée, les groupes de métadonnées natifs à retirer.
4. Relocaliser les pointeurs standards et secondaires de `0x0002`, mettre à jour les compteurs/mirroirs de suppression de pistes, puis purger les records et groupes capturés.
5. Sérialiser de nouveau, calculer l'offset final de `0x0002` et patcher `0x0001`.
6. Préserver l'en-tête déchiffré, rechiffrer une copie dans un fichier temporaire, rafraîchir tous les `original_offset`, puis remplacer atomiquement la destination.
7. Après succès, mettre à jour `self.data` avec les octets déchiffrés, vider `_removed_offsets` et les types associés, puis stocker le chemin absolu de sortie dans `file_path`. `save()` retourne `None`.

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
| `0x2602` | `0x0e` | Définition d'un chemin I/O live; seul le chemin mono autonome sélectionné est interprété par le reader de sortie. |
| `0x2603` | `0x02` | Catalogue compté de chemins I/O live. |
| `0x260a` | `0x01` | Playlist plate d'automation de volume. |
| `0x260d` | `0x05` | État/automation de piste, contenant aussi la sortie principale observée. |
| `0x260e` | `0x09` | Descripteur de sortie principale; profils bus mono/stéréo stricts (§4.1.3–4.1.8). |
| `0x2619` | `0x09` | Nom interne de piste. |
| `0x261b` | `0x0d` | Configuration de slot contenant identité et état de piste. |
| `0x261c` | `0x04` | Définition de piste et automation. |
| `0x2628` | `0x04` | Nom et attributs d'un clip ou groupe. |
| `0x2629` | `0x0b` | Définition de clip audio. |
| `0x262a` | `0x01` | Liste globale comptée des clips audio. |
| `0x262b` | `0x01` | Définition de Clip Group. |
| `0x262c` | `0x01` | Liste globale comptée des Clip Groups. |
| `0x262f` | `0x02` | Géométrie plate de fondu. |
| `0x2630` | `0x01` | Liste globale comptée des géométries. |
| `0x2637` | `0x01` | Dictionnaire plat de records de Clip Gain statiques; enveloppe quatre nœuds préservable dans l'extension non publiée §9.2. |
| `0x4826` | `0x01` | Sous-bloc du modèle de marqueur; deux occurrences. |
| `0x4827` | `0x01` | Sous-bloc du modèle de marqueur. |

## 4. Pistes et événements de timeline

### 4.1 Map `0x1054` et playlist `0x1052`

La timeline principale est l'unique racine `0x1054`. L'absence de cette racine produit une liste de pistes vide; plusieurs racines sont ambiguës.

- Le premier segment de `0x1054` commence par le nombre de `0x1052` directs en UInt32 LE.
- Le premier segment de chaque `0x1052` est `[name_len UInt32][name UTF-8][event_count UInt32]`.
- Le nom explicite doit être UTF-8 et non vide, sauf le profil anonyme mono en lecture seule détaillé ci-dessous. Le compteur doit toujours égaler le nombre de `0x1050` directement enfants, même pour une playlist sans nom.
- Les segments bruts et trailers non interprétés conservent leur position.

`get_tracks()` retourne les noms de toutes les playlists de la timeline principale, après validation complète de la map et de tous ses compteurs. Il ne filtre pas les slots masqués par l'interface Pro Tools. Sans racine `0x1054`, il retourne `[]`.

**Basic Folder, observation native en lecture seule** : dans la paire 48 kHz / enum `0x09` vérifiée, le dossier `FOLDER_TEST` possède deux miroirs `0x251a` sous `0x2519`. Leur premier octet vaut `0x0b` (les miroirs Audio valent `0x00`), la longueur du nom est le UInt32 à `+2`, et le nom commence à `+6`. Le dossier ne possède aucun `0x1052` principal : le compteur `0x1054` reste 2 et ses playlists `A_OUTSIDE` / `B_INSIDE` restent nommées, même après placement de B dans le dossier. Le lecteur retourne ces deux noms, pas le nom du Basic Folder. Les deux clips `MEDIA`, de longueur 1355354 et offset source 0, restent à 1729728000 et 1730208480 échantillons; la définition audio et le WAV sont inchangés. Deux cycles API sans mutation par session sont byte-identiques et conservent tous les champs de dossier opaques. Aucune création, modification ou reconstruction de dossier n'est implémentée; l'ouverture, la lecture, la sauvegarde et la réouverture manuelle de la copie API dans Pro Tools sont confirmées. Le fichier resauvegardé conserve les deux playlists, les deux miroirs de dossier et les clips, puis passe deux nouveaux cycles API byte-identiques. Cette observation ne valide ni les Routing Folders, ni l'imbrication, ni les playlists anonymes AAF. Le lecteur courant n'ajoute pas le fallback de catalogue `0x251a` de la contribution.

**Playlists anonymes mono, extension de lecture non publiée** : une paire native Pro Tools → AAF consolidé → Pro Tools, 48 kHz / enum `0x09`, reproduit deux playlists principales dont le header est exactement `[0 UInt32][event_count UInt32]`, soit 8 octets. Les pistes `A_OUTSIDE` / `B_INSIDE` restent décrites dans plusieurs miroirs indépendants. Le helper `_validated_anonymous_mono_track_names(N)` résout leurs noms seulement si toutes les playlists principales sont anonymes et que le profil ci-dessous concorde. L'égalité de nombres seule ne suffit pas; aucun fallback mono/stéréo du contributeur n'est intégré.

Chaque racine ci-dessous doit être unique. Les offsets de queue `T` sont relatifs à la fin du nom UTF-8 du record concerné, et non au bloc complet. Les noms sont non vides, sans NUL, UTF-8 strict; les noms de descripteur et identités UI sont uniques.

| Structure | Validation exacte du resolver |
|---|---|
| `0x1015 → 0x1014` | Premier segment brut de 4 octets, compteur UInt32 `N`; `N` descripteurs directs, chacun avec un seul segment brut. Nom : longueur UInt32 à `+0`, bytes à `+4`. Queue `T` de 39 octets : `T[0:5] = 00 01 00 00 00`; UInt32 à `T+5` et `T+30` égaux à l'ordinal `i` (zero-based); `T[11:15] = 2A 00 00 00`. Les autres champs sont opaques; l'identité propre au descripteur n'est pas assimilée à l'identité UI. |
| `0x2107 → 0x210b` | Préfixe brut de 13 octets, octet `+8 = 01`, compteur UInt32 à `+9 = N`; `N` entrées directes à un segment brut. Nom : longueur à `+4`, bytes à `+8`, identique au descripteur de même ordinal. Queue de 32 octets : magic à `T+4`, identité UI de 8 octets à `T+8`, unique et non nulle. |
| `0x2519 → 0x251a` | Exactement `2N` miroirs directs. Les items `1..N` et `N+2..2N+1` sont les deux familles; item `N+1` = compteur brut de 4 octets égal à `N`. Chaque premier segment commence par `00 00` (Audio), longueur du nom UInt32 à `+2`, bytes à `+6`, même nom ordonné que `0x1014`/`0x210b`. Queue de 42 octets : six zéros initiaux (profil mono), magic à `T+6`, identité UI à `T+10`, UInt32 à `T+18 = i+1`, magic à `T+28`, même identité à `T+32`. Les champs restants, agrégat initial et flags UI sont préservés, pas normalisés. |
| `0x2624 → 0x261c → 0x261b → 0x102d → 0x2619` | Préfixe brut de 4 octets, compteur `N`, `N` slots directs. Chaque étape du chemin contient exactement un enfant du type suivant, et chaque slot contient exactement un `0x2619` au total. Dernier segment de `0x2619` : brut de 18 octets, magic à `+4`, identité à `+8`, identique à l'identité UI du même ordinal. Le libellé historique/configuration du premier segment reste opaque. |

Les deux magics de miroir et celui du slot valent `2A 00 00 00`. Les compteurs `0x1054`/`0x1052` sont validés **avant** la résolution. Les playlists nommées gardent leur chemin historique, sans exiger ce nouveau profil de catalogue. Un mélange nommé/anonyme, un header anonyme de longueur différente de 8, une piste stéréo ou non Audio, un folder, un ordinal d'affichage masqué/réordonné, un nom/identifiant/compteur contradictoire ou une structure inconnue provoque une erreur explicite; aucune playlist n'est silencieusement omise par ce resolver.

`get_tracks()`, `get_timeline_clips()` et `get_timeline_clip_groups()` demandent explicitement `_validated_main_playlists(allow_anonymous=True)` (ou le même paramètre du validateur d'événements). Les noms sont uniquement ceux des tuples de lecture : headers, octets opaques, événements, catalogues et pointeurs ne changent pas; aucun cache de correspondance. Le défaut `allow_anonymous=False` reste celui des validateurs de mutation : les writers dépendant de la map principale refusent toujours `Track name cannot be empty.`. Les autres méthodes ne changent pas de contrat. L'API ne lit/construit pas l'AAF et ne convertit pas les médias.

La référence possède quatre définitions de clip et deux médias physiques après réimport (une définition/un média avant). Ses placements conservent longueurs 1355354, offsets source 0, débuts 1729728000 / 1730208480, fins 1731083354 / 1731563834; noms `MEDIA-01` / `MEDIA_01-01` et fichiers `aafzhhsBMCSpvcmRYPY.wav` / `aafyvqhCQCSpvcmRYPY.wav`. Les WAV consolidés sont byte-identiques entre export et réimport; leur média contient un sample supplémentaire déjà présent à l'export, sans changement de longueur de placement. Les deux PTX passent deux cycles API byte-identiques. L'ouverture et la sauvegarde de la copie API dans Pro Tools sont confirmées. Le PTX resauvegardé garde ses deux headers anonymes, ses quatre définitions, deux médias et sa géométrie exacte; il passe deux nouveaux cycles API byte-identiques, couverts par une quatrième régression native optionnelle. Les WAV restent inchangés. Lecture audio et réouverture manuelles également confirmées; aucune compatibilité générale avec toutes les sessions AAF, stéréo ou folders anonymes n'est revendiquée.

### 4.1.1 Pool de pistes précréées : renommage et visibilité

L'API ne crée ni ne réordonne de piste. Une application peut employer un pool de pistes Audio déjà créées dans une template Pro Tools : `rename_track()` met à jour les miroirs de nom vérifiés, `set_visible_tracks(track_names)` affiche exactement les slots demandés, dans l'ordre fourni, et `delete_tracks()` peut retirer les slots vides du profil strict documenté en §4.1.2.

La visibilité native est dupliquée dans trois emplacements qui doivent rester cohérents :

- les deux familles de `0x251a` sous `0x2519` : l'octet après l'identité `2A 00 00 00` vaut l'ordinal one-based de l'affichage, ou `0` si la piste est masquée; le flag court de 11 octets passe respectivement à `... 01 01 00 00 FE FF` ou `... 00 01 00 00 FE FF`;
- l'entrée agrégée `0x2519`, qui contient le même ordinal pour chaque piste;
- l'état `0x2589` : UInt16 LE à `+2` vaut `1` (affichée) ou `0` (masquée), et le payload court final vaut respectivement `00 00 00 00 00` ou `01 00 01 00 00`.

Dans le record court de 11 octets de `0x251a`, les neuf premiers octets ont la forme vérifiée `00 00 00 00 | visible | 01 00 00 00`; l'octet `visible` à `+4` est le seul modifié par l'API. Le UInt16 final (`+9..+10`) est opaque et peut varier selon la piste dans une même template (par exemple `FE FF`, `1E 00`, `2D 00`). `set_visible_tracks()` le conserve byte-for-byte et ne l'utilise jamais comme constante d'identification.

`set_visible_tracks()` exige une liste non vide, sans doublon, composée uniquement de noms de pistes existants. Il ne modifie ni le nombre de playlists, ni les offsets, ni le catalogue média, et restaure transactionnellement l'arbre en cas de profil ambigu ou incomplet. Les tests manuels ont validé l'affichage des pistes 1, 2, 3 et 9 dans un pool natif de dix pistes.

Un pool de pistes nouvellement créé doit être ouvert puis sauvegardé normalement une fois dans Pro Tools avant son premier `rename_track()`. Le profil UI pré-normalisation observé dans certaines templates de pool est lisible et peut servir au contrôle de visibilité, mais un renommage y ferait rejeter la map complète de pistes par Pro Tools. L'API reconnaît ce profil exact et retourne `ValueError` avant toute mutation; elle ne tente pas d'imiter une normalisation interne non documentée.

### 4.1.2 Suppression de pistes Audio vides (`delete_tracks`)

`delete_tracks(track_names)` est une mutation de template, pas un destructeur général de pistes Pro Tools. `track_names` doit être un itérable non vide de `str` uniques, sans NUL, qui désignent des playlists `0x1052` principales existantes. Au moins une piste Audio doit rester. Toutes les playlists principales doivent être vides : un compteur d'événements non nul ou un enfant événement provoque `NotImplementedError` avant toute écriture.

Le profil d'écriture exige une occurrence unique et cohérente de chacun des miroirs suivants. Les noms et compteurs doivent tous correspondre à l'ordre de `0x1054` :

- `0x1054 → 0x1052` : compteur et playlists principales; les playlists supprimées disparaissent.
- `0x1015 → 0x1014` : compteur, descripteurs de noms et leurs deux ordinaux UInt32; les ordinaux restants deviennent `0..N-1`.
- `0x2107 → 0x210b` : compteur et métadonnées de noms; les identités restantes sont conservées.
- `0x2519` : agrégat de noms, deux familles directes `0x251a` et leur compteur intermédiaire; les ordinaux d'affichage restants deviennent `1..N`.
- `0x2587 → 0x258a → 0x2589` : compteur et états de piste. Les états d'ID final hors du domaine compact sont retirés; les états restants sont sérialisés dans l'ordre canonique croissant `0..N-1`. Cet ordre ne dépend ni des noms, ni de leur position d'origine.
- `0x2624 → 0x261c` : compteur et slots. Les libellés opaques historiques `Audio N` sont conservés, mais les deux records d'ordinal du slot (`12` et `24` octets) sont compactés vers `0..N-1`.
- `0x202b → 0x202a` : les deux listes d'indices UInt16 deviennent exactement `0..N-1` avec leur compteur UInt32.

La table finale `0x0002` possède aussi trois miroirs bruts de cette topologie. Le writer les reconnaît seulement dans leur forme native exacte : (1) la liste de pointeurs des premiers `0x251a`, dont la longueur octet et le compteur BE sont compactés; (2) les entrées de 45 octets des seconds `0x251a`; (3) les entrées de 67 octets des slots `0x261c`. Les deux dernières entrées incorporent chacune un record standard de 15 octets. Elles sont capturées avant relocalisation, leurs records ne sont comptés qu'une fois lors de la purge, et leurs ordinaux restants sont compactés. Les six compteurs `0x251b`, le compteur voisin `0x2716`, le compteur de tête BE et le trailer final `0x261c` observé sont mis à jour suivant le profil. Une ambiguïté de taille, de type, d'ordinal, de compteur, de pointeur ou de recouvrement interrompt la transaction.

Ce chemin a été validé dans Pro Tools sur des suppressions simple en première/milieu/dernière position, doubles adjacentes et non adjacentes, aux extrémités, et triple. Il ne prend pas en charge une piste peuplée (clips, fades, groupes, automation ou tout autre événement), ni MIDI, vidéo, routing, inserts, sends, playlists alternatives ou layouts de miroirs non observés.

### 4.1.3 Lecture de sortie bus (`get_track_outputs`, non publié)

Le contrat est `[(track_name, [output_name]), ...]`, dans l'ordre des playlists principales. Ce profil expose une seule sortie principale par piste, avec une liste pour ne pas imposer une future représentation incompatible. Le bus peut être mono ou stéréo autonome (§4.1.6), mais la piste Audio reste mono. En l'absence de playlist principale (ou avec zéro piste), retourne `[]` après validation de la map. Sinon, toutes les pistes doivent correspondre au profil d'identités Audio mono corroboré de §4.1 : `_validated_main_playlists(allow_anonymous=True)` puis `_validated_anonymous_mono_track_names(N)`, avec égalité des noms ordonnés. Cela accepte les playlists entièrement nommées ou le profil entièrement anonyme vérifié; cela n'étend pas aux pistes stéréo, folders ou ordinaux UI masqués/réordonnés. Aucune liste partielle n'est retournée en cas de refus.

La résolution travaille exclusivement sur l'arbre courant : les GUID des slots et des deux familles de miroirs sont validés par le helper d'identité; aucune recherche de magic dans `self.data`, aucun ancien offset, aucun cache. Un résultat retourné n'alias pas les données de l'arbre. Le garde-fou de `rename_track()` reste indépendant : l'une des références natives I/O est lisible par ce reader mais conserve un profil UI explicitement refusé par le writer de renommage existant; ce reader ne l'assouplit pas.

**Descripteur principal** : chaque slot doit avoir exactement un `0x260d` direct sous son `0x261b` déjà validé, puis exactement un `0x260e` direct. Le slot entier doit également contenir un seul descendant `0x260e`; un second record, un autre emplacement ou une sortie absente est hors profil. Le `0x260e` doit contenir un seul segment brut :

| Offset dans le payload `0x260e` | Champ vérifié/traité |
|---|---|
| `+0` | Code UInt8 observé; conservé et non interprété/calibré par ce reader. |
| `+1..+7` | `00 01 01 00 00 00 00`. |
| `+8..+11` | `2A 00 00 00`. |
| `+12..+19` | Identité de sortie de 8 octets, non nulle et corroborée par le catalogue live. |
| `+20..+35` | 16 zéros. |
| `+36` | Longueur du nom de sortie UInt32 LE. |
| `+40` | Nom UTF-8 strict, non vide et sans NUL. |
| Après le nom | Trailer mono exact de 19 octets `00 00 FF FF FF FF FF FF FF FF 00 FF FF FF FF 00 0C 00 00`, ou trailer stéréo de §4.1.6. Doit correspondre à la largeur du bus sélectionné; autres trailers refusés, pas normalisés. |

**Catalogue sélectionné** : une unique racine `0x2603`, avec un premier segment brut de 4 octets dont le compteur UInt32 égale le nombre d'enfants directs `0x2602`. Des `0x2602` imbriqués sont refusés. Les bibliothèques `0x4501`/`0x4702` ne sont jamais utilisées comme fallback. Chaque premier segment `0x2602` doit avoir un nom de longueur UInt32 à `+2`, bytes à `+6`, UTF-8 non vide/sans NUL. Les queues des chemins non sélectionnés restent opaques, sauf l'indexation d'identité des records mono/stéréo de géométrie fixe reconnue (§4.1.6); aucune classification multicanal par port ou recherche de magic variable n'est faite.

Le nom du descripteur sélectionne exactement une entrée de catalogue. Pour une sortie mono, le préfixe doit être `02 00` et la queue `T`, relative à la fin du nom, doit être exactement longue de 42 octets. Pour une sortie stéréo, utiliser les validations distinctes de §4.1.6.

| Offset dans `T` du chemin mono sélectionné | Validation |
|---|---|
| `+0..+3` | `01 00 00 00` (profil à un canal). |
| `+4..+5` | Port A UInt16 natif, conservé sans déduire un ordinal de catalogue. |
| `+6..+7` | `FF FF` : chemin mono autonome, pas un sous-canal. |
| `+8..+13` | `01 00 00 00 00 00`, incluant le flag bus nul à `+10`. |
| `+14..+17` | `2A 00 00 00`. |
| `+18..+25` | Même identité que le descripteur de sortie. |
| `+26..+41` | 16 zéros. |

L'identité sélectionnée doit être unique parmi les records mono reconnus par `02 00`, queue de 42/magic à `T+14`, et les records stéréo 44/magic à `T+16` de §4.1.6. Noms dupliqués, alias d'identité, incohérence entre nom/ID/largeur, chemin matériel, sous-canal ou largeur sélectionnée autre que les deux profils sont refusés. Le catalogue peut contenir d'autres largeurs non sélectionnées; elles ne sont ni publiées dans une liste générale de sorties, ni transformées. `get_available_outputs()` et le writer général du contributeur ne sont pas intégrés. La branche de réutilisation de §4.1.4 n'infère aucun code; la nouvelle calibration stricte de §4.1.5 reste séparée du reader, qui n'interprète pas les codes.

**Preuve native** : dans la paire 48 kHz / enum `0x09`, les 90 entrées live `0x2602` sont inchangées. Before : `A_OUTSIDE → API_ROUTE_A`, `B_INSIDE → API_ROUTE_A`; after : seule `A_OUTSIDE → API_ROUTE_B_LONG`. Chaque piste a un seul descripteur. Pour A, code `65 → 66`, identité `376de8e605e7106c → 376de8e650f2106c`, longueur du nom `11 → 16`, taille de record `70 → 75`; le trailer reste identique. Le record de B est byte-identique. Ces codes ne deviennent pas des constantes ou une formule de writer.

Les WAV et la géométrie des clips restent exacts. La sauvegarde native a aussi changé des états UI, des identités opaques et des références de dossier : ne pas assimiler tous ces octets au routing ni déclarer, depuis cette première paire seule, qu'un simple remplacement de `0x260e` suffit à écrire un fichier valide. Quatorze régressions (dont quatre natives optionnelles) couvrent la lecture actuelle, les refus sans mutation et deux cycles API byte-identiques par PTX. L'utilisateur confirme l'ouverture, les sorties et clips attendus, la sauvegarde et la réouverture de la copie API dans Pro Tools. Le fichier resauvegardé conserve les descripteurs principaux, les 90 chemins live et la géométrie exacte des clips; deux nouveaux cycles API restent byte-identiques. Le profil de lecture est validé. L'écriture limitée de §4.1.4 est étudiée séparément; aucun support de sortie multiple, stéréo, send ou hardware n'est ajouté.

### 4.1.4 Réaffectation à un bus mono déjà utilisé (`set_track_output`, non publié)

**Statut : branches vers un bus déjà affecté et vers un bus calibré (§4.1.5) validées dans Pro Tools.** Ce writer n'est pas le writer général du contributeur. Signature : `set_track_output(track_name, output_name)`. Retourne `1` si l'unique descripteur principal cible est modifié, `0` si l'affectation est déjà identique. Aucune sauvegarde implicite; appeler `save()` séparément. Il ne crée pas de bus, de record de catalogue, de bloc ou de pointeur supplémentaire.

Les deux paramètres doivent être des chaînes non vides, sans NUL et encodables en UTF-8. Le writer exige d'abord `_validated_main_playlists()` sans autoriser les noms anonymes : le profil anonyme validé en lecture n'est pas implicitement autorisé en écriture. Puis `_validated_bus_track_outputs()` (partagé avec le reader de §4.1.3) prévalide toutes les pistes, identités et sorties de l'arbre courant, ainsi que le catalogue live. Toutes les restrictions structurelles de §4.1.3 s'appliquent. Chaque `0x260e` doit également être de `block_type 0x09`. Les extensions de largeur et pan sont décrites séparément en §4.1.6.

Il construit les correspondances depuis les **affectations actuelles**, pas depuis les seuls chemins disponibles du catalogue :

- pour un même nom de bus, tous les payloads de sortie employés doivent être byte-identiques; un code contradictoire est refusé;
- un même code UInt8 ne peut pas désigner deux bus différents parmi ces affectations;
- la piste cible doit exister dans le profil nommé vérifié;
- dans la branche de réutilisation, le bus cible est déjà affecté à au moins une piste actuelle de cette session. Sinon, les garde-fous supplémentaires de §4.1.5 s'appliquent; sa présence dans `0x2603` seule ne suffit jamais.

La branche de réutilisation n'effectue aucune calibration. Ni modulo `&0xFF`, recherche dans les bytes source, bibliothèque factory ni cache de codes n'est utilisé. Le port du catalogue ne devient pas un code de descripteur. Si la dernière affectation d'un bus disparaît, son descripteur ne reste pas en cache : un appel suivant doit satisfaire §4.1.5 pour y revenir. Les appels successifs lisent l'état actuel, sans conserver artificiellement des codes historiques.

**Écriture atomique par prévalidation et remplacement unique** : avant toute mutation, le writer prépare un payload neuf. Il conserve le préfixe `+0..+35` de la cible, remplace uniquement le code `+0` et l'identité de bus `+12..+19` par ceux du descripteur donneur vérifié, reprend la longueur/nom `+36` jusqu'à la fin du nom depuis ce donneur, puis conserve le trailer exact de 19 octets de la cible. Aucun état de piste n'est cloné. Toutes les géométries constantes et les zéros ont déjà été corroborés; le payload obtenu correspond au descripteur donneur sans alias mutable. Si identique au payload courant, retourne `0` sans remplacement. Sinon, seule l'affectation `target.items[0] = replacement` précède le retour `1`; aucune opération susceptible de validation tardive n'est effectuée ensuite.

Le bloc cible, son type, son `original_offset`, les autres descripteurs, catalogues et champs opaques de piste sont conservés. `_removed_offsets` n'est pas modifié. La taille peut augmenter ou diminuer avec le nom; le mécanisme existant de `save()` relocalise les blocs et pointeurs, plutôt qu'un patch à l'ancien offset dans `self.data`.

**Nouvelle paire native** : before contient `A_OUTSIDE → API_ROUTE_B_LONG`, `B_INSIDE → API_ROUTE_A`; after change seulement l'affectation visible d'A vers A. Le nouveau payload d'A est byte-identique au descripteur déjà présent sur B : taille `75 → 70`, code `66 → 65`, identité `376de8e650f2106c → 376de8e605e7106c`, longueur du nom `16 → 11`. Le descripteur de B et les catalogues complets `0x2603`/`0x1022` sont inchangés. Clips, positions, longueurs et WAV sont identiques. Le save natif modifie aussi des IDs opaques dans les suffixes des deux `0x261b` (pas seulement la piste routée), de l'UI, des références de dossier et des identités de médias/clips; ces données ne sont pas normalisées par le writer.

Treize tests, dont quatre natifs optionnels, couvrent paramètres/UTF-8, cas identique, prévalidation tardive sans mutation, codes contradictoires, bloc inconnu, refus de bus absents ou non calibrables, refus anonyme, appels composés depuis l'arbre courant et réutilisation sans inférence. La sortie API réduit le nom comme la référence native et ne change que le payload ciblé en mémoire. Des tests de save/reload vérifient aussi l'augmentation de taille vers le bus long déjà employé, puis deux cycles byte-identiques par sortie. **L'utilisateur confirme l'ouverture, les sorties et clips attendus, la sauvegarde et la réouverture de la sortie API à nom raccourci dans Pro Tools.** Le fichier resauvegardé conserve les deux descripteurs, les catalogues `0x2603`/`0x1022` et la géométrie exacte des clips; deux nouveaux cycles API sont byte-identiques. Le refus d'un bus inutilisé avec un seul bus distinct affecté et le cas identique restent vérifiés sur cette sauvegarde. La suite atteignait 287 tests avant §4.1.5. Cette preuve ne valide pas à elle seule une génération de code pour un bus inutilisé, ni une écriture stéréo/multi-sorties/matériel/sends, ni les profils cachés/réordonnés. La croissance du nom est couverte automatiquement, sans ouverture manuelle distincte revendiquée à ce stade.

### 4.1.5 Bus mono non affecté : calibration corroborée (non publié)

**Statut : extension locale de `set_track_output()`, sortie vers le bus inutilisé et retour au bus long validés dans Pro Tools.** Cette branche s'applique uniquement si aucun descripteur principal actuel n'emploie déjà le nom cible. Les validations communes de §4.1.3/§4.1.4 ont d'abord validé toutes les affectations existantes et la map principale nommée.

1. Exiger la racine live `0x2603` de `block_type 0x02` et tous ses `0x2602` directs de `block_type 0x0e`. Un type inconnu est refusé dans cette branche. Le compteur brut et l'absence de paths imbriqués ont déjà été contrôlés par le reader.
2. Énumérer **tous** les `0x2602` directs, sans filtrer les largeurs, sous-canaux ou sorties non sélectionnés : leur ordinal zéro-based est la position réelle dans ce catalogue, pas l'index d'une liste mono filtrée. Les noms UTF-8 et segments bruts ont déjà été contrôlés. Aucune bibliothèque `0x4501`/`0x4702` ni arbre source périmé n'est utilisé.
3. Le nom cible doit sélectionner une seule entrée. `_validated_bus_path_identity(payload, tail)`, partagé avec le reader, délègue au helper mono pour le profil de §4.1.3, ou valide le profil stéréo de §4.1.6. L'identité doit être unique parmi les entrées reconnues des deux géométries, y compris les alias mono/stéréo. Les ports sont conservés, pas utilisés comme code.
4. Exiger au moins **deux noms de bus distincts** actuellement assignés. Plusieurs pistes sur un seul bus ne suffisent pas. Pour chaque bus utilisé, calculer `base_candidate = descriptor_code - live_catalog_ordinal`. **Tous** ces candidats doivent être identiques et non négatifs; pas seulement les deux premiers.
5. Calculer `code_target = base + target_ordinal`; exiger `0 <= code_target <= 255`. Pas de masque, modulo, valeur constante ni ajustement automatique d'une base contradictoire.
6. Préparer un descripteur donneur neuf depuis le préfixe du descripteur actuel, le trailer vérifié de la largeur du bus cible, le code calibré, l'identité et le nom UTF-8 du bus validé. Pour Mono→Mono, utiliser le remplacement unique de §4.1.4; toutes les erreurs précèdent la mutation. Aucun catalogue, état de piste, cache ou pointeur supplémentaire n'est écrit dans cette branche. Mono→Stereo suit les préconditions de pan de §4.1.6.

**Corroboration native distante** : les catalogues complets `0x2603`/`0x1022` sont inchangés dans la nouvelle paire; seule A_OUTSIDE passe du bus long à ADR, B_INSIDE reste sur A. Le descripteur d'ADR écrit par Pro Tools est mono de type `0x09`, longueur 62, même préfixe/trailer que les références. Il contient l'identité `bb0655e436817669`, le nom de 3 octets et le code `0x19`.

| Chemin natif | Ordinal live zéro-based | Code de descripteur | Code − ordinal |
|---|---:|---:|---:|
| ADR | 12 | 25 (`0x19`) | 13 |
| API_ROUTE_A | 88 | 101 (`0x65`) | 13 |
| API_ROUTE_B_LONG | 89 | 102 (`0x66`) | 13 |

La valeur 13 est une observation de cette session, **pas une constante du writer**. Les deux références avant mutation corroborent la base et le nouvel after la confirme sur une entrée éloignée. Les clips, placements et WAV sont identiques. Les changements natifs d'UI, références de dossier et identités opaques ne sont pas copiés ou normalisés.

Douze régressions, dont cinq natives optionnelles : compte d'entrées opaques intermédiaires, UTF-8, appels composés, perte du second bus distinct, bases incohérentes/négatives, borne UInt8 255 et overflow sans wrap, target mono/autonome/unique, types de catalogue inconnus et accord de tous les anchors. Le payload API pour ADR correspond exactement au after natif; seul ce payload change dans l'arbre. Save/reload et deux cycles byte-identiques sont vérifiés. Le retour vers le bus long désormais non affecté se calibre depuis ADR/A et rétablit **tout le PTX before byte-identique**. **Validation utilisateur reçue pour les deux sorties : ouverture, sorties/clips attendus, sauvegarde et réouverture dans Pro Tools.** Les deux fichiers resauvegardés conservent descripteurs, catalogues I/O et géométrie de timeline; chacun passe deux nouveaux cycles API byte-identiques et un aller-retour calibré sans effet final sur ses octets. Deux régressions supplémentaires couvrent ces sauvegardes et portent la suite à 299 tests. Cela ne valide pas un moteur général d'I/O, les bus stéréo/multicanaux, hardware, Sends, catalogues factory, maps anonymes ou codes arbitraires.

### 4.1.6 Piste Audio mono vers bus stéréo autonome (non publié)

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture manuelle validés.** Il s'agit de la largeur du bus, pas d'une piste Audio stéréo. Les identités de piste restent soumises au profil mono corroboré de §4.1.3. `_validated_bus_track_outputs()` et `_validated_bus_path_identity()` ne classifient ni tous les layouts multicanaux, ni les sous-chemins, ni les sorties matérielles.

Le record sélectionné `0x2602` commence par `02 01`; nom UInt32 à `+2`, UTF-8 à `+6`. Sa queue `T` après le nom est exactement de 44 octets :

| Offset dans `T` stéréo | Validation |
|---|---|
| `+0..+3` | `02 00 00 00` (profil à deux canaux). |
| `+4..+7` | Deux ports UInt16 natifs; valeurs conservées, pas utilisées comme ordinals/codes. |
| `+8..+15` | `FF FF 01 00 00 00 00 00`, chemin autonome bus. |
| `+16..+19` | `2A 00 00 00`; ne pas employer l'offset mono `+14`. |
| `+20..+27` | Identité non nulle de 8 octets; ne pas employer l'offset mono `+18`. |
| `+28..+43` | 16 zéros. |

Le trailer stéréo `0x260e` après son nom est exactement `01 01 FF FF FF FF FF FF FF FF 00 FF FF FF FF 00 0C 01 00`, soit 19 octets. Les octets `0`, `1` et `17` du trailer passent de 0 à 1 par rapport au profil mono. Les autres champs du préfixe du descripteur restent ceux de §4.1.3. Nom, identité et largeur doivent tous concorder avec l'unique chemin sélectionné. L'index d'alias reconnaît les deux géométries fixes (mono 42/magic14, stéréo 44/magic16), même si un chemin non sélectionné est autrement invalide; une identité réutilisée entre ces records entraîne un refus. Le reader n'inspecte pas ni ne modifie le pan : il expose les sorties, pas les paramètres de pan.

**Writer Mono→Stereo :** la branche de code déjà affecté ou calibré de §4.1.4/§4.1.5 est conservée. La largeur du trailer vient du donneur/bus cible, jamais du descripteur mono actuel. Avant mutation, le slot ciblé doit contenir exactement deux descendants `0x260c`, tous deux directs dans son conteneur principal `0x260d`, de type `0x02`, chacun avec un seul segment brut de 14 zéros. Aucun état opaque ou pan préexistant non vide n'est effacé. Le premier conteneur conserve son bloc et offset, mais reçoit les items suivants; le second reste byte-identique :

```text
01
[0x260a, block_type=0x01, original_offset=0,
 payload=01 46 01 00 14 00 00 00 00 00 01 00 00 00
         02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00]
00 × 13
```

Cette lane de 30 octets contient un point statique au timestamp 0, valeur Int16 0 à `+26` (centre). Elle est reconstruite depuis ce profil natif exact, pas copiée depuis le pan d'une autre piste. Tous les refus et allocations précèdent le remplacement des items du pan et du payload de sortie. Aucun catalogue I/O, média, clip, autre piste ou offset existant n'est muté; `save()` relocalise les pointeurs existants. La comparaison native n'ajoute aucun pointeur standard `0x0002` pour la nouvelle lane et conserve les 172 records avec les mêmes métadonnées et cibles logiques.

Une affectation strictement identique retourne `0` sans pan modifié. Le retour vers un bus mono possède le profil natif strict de §4.1.7; le changement vers un autre bus stéréo possède la nouvelle branche limitée de §4.1.8, encore en attente de validation manuelle. Ce writer ne fournit pas d'API de pan ni de pistes Audio stéréo; Mono→Stereo reste limité aux conteneurs initialement vides, Stereo→Mono et Stereo→Stereo au seul pan statique centré vérifié.

**Preuve native :** deux pistes mono, 48 kHz / enum `0x09`, mêmes clips/WAV/catalogues avant/après. Pour la piste cible, bus long mono → bus stéréo entier : code `0x66 → 0x2b`, nom UTF-8 `16 → 7` octets, payload `75 → 66`, identité du chemin stéréo à T+20, trailer ci-dessus et activation du pan centré ci-dessus. L'ordinal 30 et code 43 donnent la même base 13 que les deux anchors mono, sans constante dans le code. La sortie de l'autre piste reste byte-identique. Les changements natifs de dossiers, UI, identités opaques et timestamps de sauvegarde ne sont pas transposés à l'API.

Douze tests ajoutés, dont quatre natifs optionnels : égalité exacte des nouveaux route/pan et absence d'autres mutations, UTF-8, code réel d'une destination déjà utilisée sans clonage de pan, calibration depuis les affectations mono/stéréo courantes, géométries/noms/identités/aliases/largeurs invalides, profils pan inconnus, refus des transitions non vérifiées, pointeurs natifs et deux cycles API byte-identiques sur originaux et sortie modifiée. **Validation utilisateur reçue : ouverture/sauvegarde/réouverture conforme dans Pro Tools.** La sauvegarde native conserve les route/pan exacts, les catalogues I/O et la géométrie des placements, puis passe deux nouveaux cycles API byte-identiques. Une régression supplémentaire couvre cette sauvegarde et l'appel identique sans effet. La suite atteignait 311 tests avant le retour de §4.1.7; son ancien test de refus général Stereo→Mono vérifie maintenant le refus du pan non centré. Aucun retrait du pan n'est implémenté par supposition.

Observation complémentaire de catalogue : un deuxième bus autonome créé nativement possède également le préfixe `02 01` et une queue de 44 octets strictement conforme à §4.1.6, avec ports 139/140 et identité distincte non nulle. Les sorties et pans des pistes restent inchangés lors de cette préparation; le before préparé passe deux cycles API byte-identiques. La préparation seule ne prouve pas une transition Stereo→Stereo; sa paire ultérieure et les limites d'écriture sont décrites en §4.1.8.

### 4.1.7 Retour bus stéréo vers bus mono, pan centré statique (non publié)

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture Pro Tools validés; étape clôturée.** La piste Audio reste mono. Les validations communes de §4.1.3 à §4.1.6, playlists nommées et code cible réutilisé/calibré restent obligatoires. La transition vers un autre bus stéréo est traitée séparément en §4.1.8, sans retrait du pan.

La nouvelle paire native montre uniquement, dans l'état fonctionnel de la piste ciblée :

- `0x260e` : code `0x2b → 0x66`, identité et nom du bus mono long, longueur UTF-8 `7 → 16`, payload `66 → 75`, trailer mono de 19 octets rétabli;
- premier `0x260c` : suppression de la lane `0x260a` centrée et remplacement des trois items `[01][lane][00×13]` par un seul segment `00×14`;
- deuxième `0x260c` : vide et byte-identique;
- aucun changement de la route de l'autre piste, catalogues I/O, clips, placements ou WAV.

Le writer résout les deux conteneurs exactement au chemin principal déjà validé. Il exige deux `0x260c` directs/type `0x02`, et exactement deux descendants de ce type dans tout le slot. Le premier doit avoir **exactement trois items** : segment brut `01`, enfant `0x260a`/type `0x01` avec un seul payload brut égal aux 30 octets de `_BUS_CENTER_PAN` (§4.1.6), puis segment brut de 13 zéros. Le second doit être un seul segment de 14 zéros. Une autre valeur de pan, un autre timestamp, des flags différents, de l'automation, un enfant/segment supplémentaire ou un état opaque sont refusés; aucune donnée de pan arbitraire n'est effacée.

Si la lane possède un `original_offset` existant non nul, il faut une unique racine `0x0002`. `_raw_0002_payload()` et `_validate_0002_record_layout()` valident son payload actuel; ce profil exige également des records standards non vides (un payload sans records ne prouve pas l'absence de références). Puis leurs pointeurs UInt32 LE sont examinés : **toute référence à cet offset est refusée**, pas purgée expérimentalement. La paire native conserve ses 172 records standards avec mêmes métadonnées/cibles logiques; aucun ne référence la lane retirée. Une lane créée en mémoire à offset 0 n'a pas de référence source à examiner.

Avant toute mutation, préparer le nouveau descripteur mono, les items vides du pan et des copies des métadonnées de suppression. `_collect_offsets_recursive()` et `_collect_offset_types_recursive()` enregistrent dans ces copies l'offset positif/type `0x260a` de la lane retirée. Puis seulement remplacer le payload de route, les items du premier pan, `_removed_offsets` et `_removed_block_types`. Le bloc parent, son offset et le deuxième pan restent conservés; les autres métadonnées de suppression préexistantes restent intactes. Aucun bus, record de catalogue ou média n'est créé. `save()` utilise ses mécanismes habituels de relocation/purge et réinitialise ces métadonnées après succès.

Une affectation identique retourne toujours `0` sans inspecter/modifier le pan. Après réduction réussie, un appel Mono→Stereo peut réinstaller la lane centrée depuis les conteneurs redevenus vides. Sur le before natif, ce retour composé reproduit tout le PTX **byte-identique**, y compris après enregistrement de l'offset retiré et passage par `save()`.

Dix tests supplémentaires, dont six natifs optionnels : destination mono déjà utilisée (code réel non calibré) ou inutilisée/calibrée, compositions depuis l'arbre courant, refus pan/automation/table/référence sans modification du route ni des métadonnées, exactitude route/pan native et absence d'autres mutations, conservation de l'offset parent, bookkeeping de la lane retirée, cycles API sur les deux originaux et sortie modifiée, restauration du fichier entier, sauvegarde et réouverture Pro Tools. **Ouverture/sauvegarde/réouverture manuelles confirmées** : les références natives après sauvegarde et après réouverture gardent les route/pans vides, catalogues et placements exacts; chacune passe deux cycles API byte-identiques et une composition mono/stéréo/mono byte-identique. **Suite locale 321 tests réussis; étape clôturée.** Les autres changements natifs d'UI, chemins, timestamps de sauvegarde et identités de clips restent opaques, pas transposés au writer.

### 4.1.8 Bus stéréo vers autre bus stéréo, pan centré préservé (non publié)

**Statut : comparaison native, tests API et ouverture/sauvegarde/réouverture de la sortie API dans Pro Tools validés; étape clôturée, support non publié.** La piste Audio reste mono; il s'agit uniquement d'un changement entre deux bus autonomes stéréo de géométrie §4.1.6. Le premier after reçu changeait une autre piste de mono vers stéréo : conservé, mais exclu de l'oracle de cette nouvelle transition.

La paire corrigée conserve les deux pans, la sortie de l'autre piste, les catalogues complets `0x2603`/`0x1022`, les clips/placements/WAV et les 172 records standards de `0x0002` avec leurs métadonnées/cibles logiques. Pour la seule sortie cible `0x260e`, code `0x2b → 0x67`, identité `032a39e510cc6364 → ff2ee9e6deff3bee`, longueur du nom UTF-8 `7 → 21`, payload `66 → 80` octets. Le trailer stéréo de 19 octets reste exactement `0101ffffffffffffffff00ffffffff000c0100`. Le nouveau bus est à l'ordinal 90, code 103 : même base 13 que les anchors existants, sans constante de base dans le writer. Ses ports 139/140 ne servent pas à calculer le code.

`set_track_output()` conserve les préconditions communes de map nommée, identité Audio mono, sortie principale unique, bus/nom/identité/largeur vérifiés et code réutilisé ou calibré. Après construction du descripteur neuf et rejet des contradictions, une affectation byte-identique retourne `0` avant inspection du pan. Pour une véritable transition Stereo→Stereo, résoudre le slot/`0x261b`/`0x260d` principal et exiger exactement deux `0x260c` directs/type `0x02`, sans autre descendant de ce type dans le slot. Le premier doit avoir exactement `[01][0x260a type01, payload brut unique égal aux 30 octets de _BUS_CENTER_PAN][00×13]`, le second un seul segment de 14 zéros, comme §4.1.7. Pan non centré, automation, timestamp/flags différents, segments/enfants supplémentaires et états inconnus restent refusés sans mutation.

Cette validation du pan est partagée avec la réduction Stereo→Mono, **pas ses mutations ni la vérification préalable des références de lane retirée** : aucune lane n'est retirée ici. Préparer puis remplacer **uniquement `target.items[0]`**, le payload de sortie. Conserver les deux conteneurs et leurs items/objets, la lane centrée, tous leurs `original_offset`, ainsi que les listes/dictionnaires préexistants `_removed_offsets`/`_removed_block_types`. Ne jamais cloner le pan de la piste donneuse, même si son code est réutilisé. Aucun nouveau pointeur, catalogue, bus, média ou clip n'est généré. `save()` relocalise les pointeurs existants selon son pipeline habituel.

Onze régressions, dont quatre natives optionnelles : target inutilisé UTF-8/code calibré, code réel arbitraire réutilisé, pan donneur non cloné, objets/offsets/bookkeeping préservés, compositions avec changement de largeur, refus pan/target/alias/calibration tardifs sans mutation, manque de second bus distinct et overflow UInt8 sans wrap, no-op, exactitude native du seul descripteur remplacé, conservation des records standards et deux cycles API byte-identiques sur les originaux/sortie modifiée. Le retour vers le premier bus stéréo reproduit **tout le before byte-identique** après croissance/réduction du nom. **Ouverture/sauvegarde/réouverture manuelles confirmées** : le fichier resauvegardé conserve routes, pan centré, catalogues, placements et cibles de pointeurs, puis passe deux nouveaux cycles API byte-identiques et une composition entre les deux bus reproduisant tout le fichier. **332 tests réussis avec `-W error`, syntaxe Python 3.8 vérifiée; étape clôturée.** Les autres différences natives de sauvegarde restent opaques et ne sont pas copiées au writer. Aucun support de pan arbitraire, pistes Audio stéréo, routing matériel/multicanal/sends ou création de bus n'est revendiqué.

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
| Placement audio | observé/recopié; `0x0a` dans le builder audio | `00 01 01`, ou la variante observée `01 01 01` lorsque `0x104f[15] == 0x03` |
| Macro Clip Group | observé/recopié | `00 00 01` |
| Fondu | `0x0a` | `01 01 01` |

Une macro de groupe n'est jamais un clip audio, même si son ID numérique est égal à un ID de `0x262a`. Les événements d'autres types sont préservés mais ne sont pas retournés par `get_timeline_clips()`.

`get_timeline_clip_groups()` est le lecteur public dédié aux macros visibles de la timeline principale. Il valide une unique racine `0x262c` (compteur UInt32 et `0x262b` directs), attribue l'ID de groupe par ordinal zéro-based dans cette liste, puis parcourt les playlists validées de la racine principale `0x1054`. Pour chaque événement `0x104f[15] == 0x03` dont la queue secondaire est exactement `00 00 01`, il lit l'ID UInt32 LE à `+2` et le timestamp UInt64 LE à `+7`; la durée provient du payload `0x2628` de la définition `0x262b` correspondante. Le résultat est trié par `(start_samples, track)` et contient chaque occurrence, y compris les placements répétés d'un même groupe : `group_id`, `group_name`, `track`, `start_samples`, `length_samples`, `end_samples`, `start_timecode`, `length_timecode`, `end_timecode`. Un ID de macro absent de `0x262c` est une incohérence rejetée. Cette méthode est strictement en lecture seule et ne modifie pas le contrat audio de `get_timeline_clips()`.

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
| `0x1001` (non publié) | `virtual` | UInt8 à `A+5` | `A+6` |
| `0x2000`, `0x2001` | même règle du bit faible | UInt16 à `A+5` | `A+7` |
| `0x3000`, `0x3001` | même règle du bit faible | UInt24 à `A+5` | `A+8` |
| `0x4001` | `virtual` | UInt32 à `A+5` | `A+9` |

Le sélecteur indépendant à `A+2` donne la largeur de la longueur : `0x10` = UInt8, `0x20` = UInt16, `0x30` = UInt24 et `0x40` = UInt32. Cette règle s'applique à toutes les familles ci-dessus. Des sessions de production ont confirmé les variantes parent `0x2000`/`0x3000`, les sélecteurs compacts `0x10`/`0x20`, ainsi que le passage de `0x3001` à `0x4001` sur une source longue.

Pour l'écriture `relink_clip()`, le marqueur suivant le sélecteur est normalement `(high_nibble(flag) | 0x04)`, suivi de `0x08`. Une variante native Pro Tools/RX validée ajoute précisément `flag=0x3000`, sélecteur `0x20`, marqueur `0x44`, constante `0x08` : elle contient donc un offset source UInt24, une longueur UInt16 et la référence incorporée UInt32 à `A+5+3+2`. Sur le corpus validé, cette référence vaut la référence `0x2106` du média physique plus l'offset source. Le writer conserve cette géométrie et recalcule la référence incorporée avec la même équation pour le nouveau média.

Les trois premiers octets doivent être distingués sans ambiguïté : `01 30 40` signifie flag UInt16 `0x3001`, puis sélecteur de longueur UInt32 `0x40`; `01 40 30` signifie le véritable flag UInt16 `0x4001`, puis sélecteur de longueur UInt24 `0x30`. Tout autre flag audio — notamment `0x1000` et `0x4000`, non vérifiés dans le corpus natif local — ou sélecteur est rejeté. La prise en charge d'une longueur UInt8 est indépendante de celle de l'offset source UInt8, ajouté uniquement pour `0x1001`. La valeur maximale d'un champ UInt8 est `255`, celle d'un UInt16 `65 535`, celle d'un UInt24 `16 777 215` et celle d'un UInt32 `4 294 967 295`.

La référence native de trim du début à 48 kHz / enum `0x09` confirme précisément `01 10 30 44 08` à partir de `A` : offset UInt8 de 100 à `A+5`, longueur UInt24 de 8680572 à `A+6`, deux UInt32 égaux à 1729728100 à `A+9` et `A+13`. Le placement passe de 1729728000 à 1729728100, et sa fin reste 1738408672. Le parent conserve ses attributs et le WAV reste byte-identique. Ce marqueur `0x44` et les autres champs sont conservés, pas normalisés vers un marqueur supposé `0x14`. L'ouverture, la lecture, la sauvegarde et la réouverture de la copie API dans Pro Tools sont confirmées. Le fichier resauvegardé conserve exactement offset, durée et placement, puis passe deux cycles API sans mutation byte-identiques; la régression native couvre aussi cet état après Pro Tools. `get_relink_write_status()` retourne `supported=False`, code `unsupported_clip_layout`; `relink_clip()` refuse ce profil sans mutation ni clone WAV. Aucun nouveau writer UInt8 ni extension des tables de relink n'est intégré.

Le lecteur accepte toute combinaison observée de la matrice ci-dessus. Le writer demeure volontairement plus restreint : il ne produit que les layouts explicitement décrits dans les sections Split et Trimming/sous-clips.

Le parent issu de l'import mono-float utilise l'un des deux profils stricts du builder. Le profil historique `00 00 30 04 00` à partir de `A` porte une durée UInt24 à `A+5` et une référence temporelle UInt32 à `A+8`. Le profil `00 00 40 44 00` porte une durée UInt32 à `A+5` et deux références temporelles UInt32 à `A+9` et `A+13`. `build_audio_session()` sélectionne le profil depuis le template, conserve ses champs opaques et produit un parent à `src_offset=0`, avec un nom explicite ou, par défaut, le stem physique suivi de `.A1`. La durée et la référence BWF doivent tenir respectivement dans la largeur du profil et dans UInt32.

Une définition clonable contient aussi un segment d'identité brut de 48 octets dans `0x2629` :

- Clip ID UInt32 à `+0`.
- UUID RFC 4122 aux octets `+22..+37`.
- `create_subclip()` exige exactement une occurrence de ce segment. `split_clip()` parcourt les segments mutables de 48 octets et remplace l'UUID de chacun; les fixtures vérifiées en contiennent exactement un, mais le code du split ne fait pas lui-même ce contrôle d'unicité.

Dans les profils historiques vérifiés, l'index de Clip Gain signé Int32 se trouve dans les quatre octets commençant à `len(payload_2628) - 6`. L'index alternatif annoncé à -22 avec queue UUID moderne n'est pas validé; aucun fallback de distance par simple validité numérique n'est implémenté. Cet audit n'ajoute pas encore de détecteur structurel sûr de cette queue : ne pas utiliser le writer sur ce profil non vérifié, même si les octets lus à -6 semblent un index plausible.

### 5.2 Définition de groupe `0x262c → 0x262b → 0x2628`

Le flag vérifié d'un Clip Group est `0x5000`. Sa longueur totale est une valeur 24-bit à `A+10`; les champs observés à `A+13` et `A+17` sont temporels, mais le lecteur public ne les consomme pas. Un groupe est retourné avec `type="group"` et `src_offset=0`.

### 5.3 `physical_filename`

Le layout standard possède un lien physique ordinal exact : le segment brut direct de 104 octets d'un `0x2629` stocke à `+96` un index UInt32 little-endian dans les enfants directs `0x1003` de l'unique catalogue racine `0x1004`. Le premier segment de `0x1004` contient leur compteur UInt32; le premier segment de chaque `0x1003` contient son ordinal one-based. Un corpus de production contient des index bien supérieurs à 255 et confirme que les quatre octets `+96..+99` appartiennent au champ; le writer n'impose donc aucune limite artificielle à 255.

Le riche enfant direct `0x103a` correspondant commence par deux compteurs, le marqueur `0x01`, le nom UTF-8 `Audio Files`, quatre octets opaques, puis un enregistrement ordonné par `0x1003` :

```text
02 00 00 00 00 | name_len UInt32 | filename UTF-8 | suffixe de 4 octets
```

Deux suffixes ont été observés pour les WAV : `45 56 41 57` (`EVAW`) et `00 00 00 00`. Tous les enregistrements d'un même catalogue doivent employer la même variante. Le writer conserve exactement la variante source lorsqu'il insère un nouveau nom et refuse les suffixes inconnus ou mélangés.

La liste est suivie d'une queue hiérarchique. Elle ne représente pas un chemin de système de fichiers et ses libellés ne doivent jamais servir à construire le chemin du WAV :

```text
00 FF FF FF FF
pour chaque nœud i, i = 1..K :
    label_len UInt32 | label UTF-8 | opaque 4 octets | 01 | node_count_i UInt32
puis :
    terminal_label_len UInt32 | terminal_label UTF-8 | 00 00 00 00
```

`K` doit être au moins 1. Pour `N` fichiers, `node_count_i = N+i`; les deux compteurs initiaux valent respectivement `N+K+2` et `N+K+1`. Le writer valide toute la queue jusqu'au libellé terminal et incrémente les deux compteurs initiaux ainsi que chacun des `K` compteurs de nœud quand il ajoute un fichier.

Le corpus comparatif minimal possède `K=1` et les relations `N+3`/`N+2`/`N+1`. Le diagnostic Pro Tools a confirmé qu'ajouter un fichier sans incrémenter ce troisième compteur laisse la piste visible dans la fenêtre d'import, mais bloque Pro Tools au chargement du catalogue média. Une correction isolée de ce compteur, puis une sortie complète avec WAV généré, se sont ouvertes correctement.

Un corpus de production possède `K=3`, ce qui confirme la règle générale à plusieurs niveaux. Les libellés et compteurs de cette queue sont des métadonnées PTX opaques, pas des dossiers du poste courant. Le dossier physique conventionnel reste le dossier frère `Audio Files` de la session; `relink_clip()` reçoit toutefois les chemins source et destination explicitement et ne les déduit pas de ces libellés internes.

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

**Limite et contournement observés (2026-07-21).** Un `Save Copy In…` Pro Tools d'un corpus Premiere conserve le header `0x2106` de 173 octets, son texte Adobe Premiere et la géométrie virtuelle `0x3001/0x30/0x04/0x08`; il ne convertit pas le clip en média natif. `Consolidate Clip` crée en revanche un header 151 octets et un clip parent natif. Une sortie relinkée de cette session consolidée s'est ouverte correctement dans Pro Tools. Le relink écrit du layout hérité reste non spécifié; la consolidation préalable est le contournement validé.

`relink_clip(track_name, clip_name, placement_start_samples, new_clip_name, source_audio_path, new_audio_path, replacement_audio_path=None)` reproduit le cas comparatif Pro Tools où deux placements partagent d'abord un WAV, puis l'un d'eux référence une identité physique indépendante. Le ciblage exige une définition de nom unique et exactement un événement audio sur la piste demandée dont le timestamp UInt64 égale `placement_start_samples`; la valeur doit aussi tenir sur UInt32 pour les champs temporels des layouts relink vérifiés. Le dernier argument facultatif fournit un WAV rendu dont seul le chunk PCM `data` sera installé dans le clone.

`get_relink_write_status(track_name, clip_name, placement_start_samples)` est le préflight public strictement **en lecture seule**. Il résout le même placement exact, valide d'abord la géométrie `0x2628` que le writer accepte, puis inspecte la définition `0x2629`, son index média `+96`, l'entrée directe `0x1003` et le header brut `0x2106`, sans modifier l'arbre, le WAV ni la table `0x0002`. Une géométrie décodée mais non écrivable retourne `{"supported": false, "code": "unsupported_clip_layout"}`; une géométrie absente ou tronquée retourne `unverified_clip_layout`. Pour un virtuel utilisant un header `0x2106` autre que les longueurs natives 142/151, le résultat est `{"supported": false, "code": "premiere_virtual_media", "detail_header_length": N}`; le corpus Premiere observé retourne `N=173`. Si le catalogue ou le header ne peut pas être vérifié sans heuristique, le résultat est également `supported=false` avec un code `unverified_*`. Un résultat positif garantit que le writer ne rejettera pas la géométrie `0x2628`, mais toutes les autres préconditions de `relink_clip()` restent obligatoires.

Préconditions binaires :

- un unique `0x1004`, dont le compteur égale ses `0x1003` directs;
- un unique `0x103a` ordonné suivant le format de 5.3, avec exactement un nom par `0x1003`, au moins un nœud de queue, les compteurs de nœud consécutifs `N+1..N+K` et les deux compteurs initiaux égaux à `N+K+2` et `N+K+1`;
- des ordinaux `0x1003` one-based consécutifs;
- un enregistrement fixe de lien média de 104 octets dans le `0x2629` source, avec index UInt32 little-endian à `+96`, et un enregistrement fixe d'identité de 48 octets. Le parseur générique peut avoir découpé un `5A xx 00 00 00 00 00` fortuit comme bloc vide : `_validated_2629_fixed_records()` resérialise alors tous les items situés entre `0x2628` et `0x4403`, puis après `0x4403`, et exige exactement 48 puis 104 octets;
- soit le layout racine spécial `00 00 30 44 00`, avec deux références temporelles UInt32 identiques, soit un layout de production **natif Pro Tools** parent ou virtuel de flag `0x0000`/`0x0001`/`0x2000`/`0x2001`/`0x3000`/`0x3001`/`0x4001`, dont les deux octets de géométrie valent normalement `(high_nibble | 0x04), 0x08` puis un sélecteur de longueur vérifié. L'exception écrivable validée est `0x3000`/`0x20`/`0x44`/`0x08` (offset UInt24, longueur UInt16). Les flags `0x0000`, `0x2000` et `0x3000` sont donc aussi relinkables; leurs offsets source sont respectivement de largeur 0, UInt16 et UInt24. Le writer conserve le flag, la queue opaque et la géométrie temporelle existante. Les marqueurs Premiere observés `0x3001`/`0x30`/`0x04|0x84`/`0x08` sont décodés à la lecture seulement : aucun relink écrit n'est spécifié pour eux;
- dans le `0x1003` modèle de relink natif, un `0x1001` brut de 31 octets — réassemblé si ses octets ont fortuitement formé un faux bloc vide — et un `0x2106` de 142 ou 151 octets avec une queue de 58 octets. Les headers Premiere de 169/173 octets sont observés et préservés par le lecteur, mais leur écriture n'est pas validée;
- un fichier RIFF/WAVE little-endian possédant exactement un chunk `bext` d'au moins 412 octets, `minf` d'au moins 16, `regn` d'au moins 92 et `umid` d'au moins 24;
- une référence temporelle UInt64 à `bext+338` égale soit aux deux UInt64 à `regn+44`/`regn+52`, soit au second lorsque le premier vaut zéro dans le layout de production, et égale au UInt32 du `0x2106` physique source. La paire racine `0x2628` est toujours validée; une référence incorporée virtuelle peut être spécifique à l'application et n'est pas utilisée pour valider le WAV. Lorsque le stem `regn` se termine avant `+76`, les deux tokens UInt64 à `+76`/`+84` doivent être identiques; le layout de production à stem long ne possède pas ces champs exploitables.

Le basename source doit correspondre au nom indexé par le PTX. Si le stem UTF-8 incorporé dans `regn` correspond au stem physique, il est remplacé; une abréviation de production divergente, par exemple `Rdy` contre `Ready`, est préservée sans être interprétée comme une erreur. Le nouveau nom physique doit être absent du catalogue, le chemin de destination absent et son dossier existant. Dans l'usage normal, l'appelant construit les deux chemins à partir du dossier `Audio Files` frère du fichier PTX; les libellés de la queue `0x103a` n'interviennent jamais dans cette résolution. Le stem UTF-8 du nouveau WAV doit différer du stem source tout en ayant exactement la même longueur, afin de rester compatible avec les champs fixes de l'identité physique.

Mutation PTX :

1. Supprime transactionnellement tout événement audio visible dont l'ID est hors de la Clip List courante, en décrémentant sa playlist et en collectant ses offsets pour la purge `0x0002`; cette normalisation empêche qu'un nouvel ordinal ne réactive un événement obsolète. Clone ensuite le `0x1003` source **uniquement dans un catalogue natif vérifié**, efface tous ses offsets, lui attribue l'ordinal suivant et synchronise ses identifiants `0x1001`/`0x2106` avec le nouvel UMID du WAV. Le FILETIME à `+29` est arrondi vers le bas à la seconde et le second vaut exactement une seconde de moins; tous deux ont donc un reste nul modulo `10 000 000`. Cette règle ne décrit pas les catalogues Premiere.
2. Incrémente les deux compteurs d'en-tête et tous les compteurs de nœud de la queue `0x103a`, y insère un enregistrement conservant le suffixe `EVAW` ou nul du catalogue, incrémente le compteur `0x1004` et place le clone après le dernier `0x1003`.
3. Clone le `0x2629`, normalise ses deux enregistrements fixes, génère un nouvel ID ordinal à `identity+0` et un UUID aux octets `identity+23..+38` — l'octet sentinelle `+22` est préservé —, remplace le nom `0x2628`, puis écrit le nouvel index physique UInt32 à `media_link+96`. La nouvelle référence média, dans le PTX et dans le WAV, vaut strictement `placement_start_samples − src_offset`; une racine réécrit sa paire temporelle au placement, tandis qu'un virtuel conserve intégralement sa queue et sa référence incorporée.
4. Incrémente `0x262a`, insère le clone après la dernière définition et remplace uniquement l'ID UInt32 à `0x104f+2` de l'événement ciblé.
5. Aucun enregistrement `0x0002` n'est ajouté : les neuf nouveaux blocs ont des offsets neufs et la référence Pro Tools comparative conserve une table `0x0002` de taille identique.

Mutation WAV :

- le fichier complet est copié vers un temporaire dans le dossier de destination; par défaut, le chunk PCM `data` reste identique;
- dans la référence d'origine de 32 octets de `bext`, seuls les quatre octets ASCII `+294..+297` (`bext+288+6..9`) sont renouvelés, conformément à la comparaison native; le reste est préservé. `bext` reçoit aussi la date/heure locale, une référence temporelle égale à `placement_start_samples − src_offset` et le nouvel UMID; `minf` reçoit le FILETIME exact non arrondi;
- `regn` préserve son mode de références : `(new,new)` dans le layout court et `(0,new)` dans le layout de production. Les deux tokens opaques sont renouvelés seulement lorsqu'ils existent avant le stem; le stem est remplacé seulement lorsqu'il concordait avec le nom physique. `regn` et `umid` reçoivent l'identifiant compact concordant;
- si `replacement_audio_path` est fourni, son RIFF doit posséder un unique `fmt ` d'au moins 16 octets et un unique `data`. Seuls PCM `0x0001` et WAVE_EXTENSIBLE `0xFFFE` avec sous-format PCM `1` sont admis. Canaux, fréquence, byte rate, block align, bits par échantillon et taille exacte de `data` doivent égaler le clone; les octets PCM sont copiés par blocs de 1 Mio sans remplacer les autres chunks;
- le remplacement final est atomique et ne peut écraser un fichier existant.

La mutation de l'arbre PTX est transactionnelle. Une erreur avant le remplacement final supprime le temporaire et restaure l'arbre. Après succès, le WAV existe déjà mais la session n'est encore qu'en mémoire : l'appelant doit exécuter `save()` et nettoyer le WAV lui-même si cette sauvegarde ultérieure échoue. Le retour est un dictionnaire contenant le nouvel ID de clip, son nom, l'index physique et le nom du WAV.

### 6.5 Construction d'une session audio depuis un template

`build_audio_session(template_ptx_path, clip_specs, output_session_directory, session_name=None)` produit un dossier autonome contenant un PTX et son sous-dossier `Audio Files`. Cette fonction de module est le seul authoring de session complet exposé; elle ne construit pas une session à partir de zéro, mais remplace et clone les structures d'un template Pro Tools strictement validé.

L'API est volontairement indépendante des conventions métier des applications clientes. Elle ne déduit ni piste, ni ordre, ni regroupement à partir des noms de fichiers. Le manifeste ordonné `clip_specs` constitue l'unique source de vérité pour ces décisions.

#### Contrat du template

Le template doit respecter simultanément les invariants suivants :

- session little-endian, `sample_rate == 48 000` et `frame_rate_enum == 0x09` (23,976 non-drop);
- au moins une playlist visible; tous les noms de pistes visibles sont uniques et l'arbre entier est dépourvu de `0x1050`, y compris dans les timelines cachées;
- exactement une définition directe `0x2629` sous l'unique `0x262a`, sans placement, utilisant l'un des deux profils d'import natifs ci-dessous;
- exactement un média direct `0x1003` sous l'unique catalogue `0x1004`, un seul filename physique ordonné et un lien média de 104 octets dont l'index UInt32 à `+96` vaut zéro;
- un seul `0x1001` et un seul `0x2106` correspondant exactement au même profil;
- un catalogue de noms `0x103a` conforme à 5.3, avec suffixe `EVAW` ou nul et au moins un nœud hiérarchique.

Les profils sont stricts; un mélange de leurs longueurs, préfixes ou offsets est rejeté. `ProToolsSession.validate_audio_import_template()` applique ce diagnostic sans modifier la session et retourne `{"profile": <nom>, "tracks": [<pistes visibles>]}`. `build_audio_session()` sélectionne ce même profil avant d'inspecter les WAV, afin que la borne de durée corresponde à la géométrie réellement écrite.

| Profil | Parent `0x2628` | Identité `0x1001` | Détail `0x2106` |
|---|---|---|---|
| `native_float_15_142` | Préfixe `00 00 30 04 00`; durée UInt24 à `+5`; référence UInt32 à `+8`. | 15 octets : 48 kHz `+0`, mono `+4`, 32 bits `+5`, durée UInt24 `+6`, cinq zéros `+9..+13`, float `0x03` `+14`. | Header 142 + queue 58; FILETIME `+29`, référence `+91`, FILETIME précédent `+96`, UUID `+126..+141`. |
| `native_float_31_151_u32` | Préfixe `00 00 40 44 00`; durée UInt32 à `+5`; deux références UInt32 à `+9` et `+13`. | 31 octets : 48 kHz `+0`, mono `+4`, 32 bits `+5`, durée UInt32 `+6`, quatre zéros `+10..+13`, float `0x03` `+14`; la queue opaque `+15..+30` est préservée. | Header 151 + queue 58; FILETIME `+29`, référence `+100`, FILETIME précédent `+105`, UUID `+135..+150`. |

Le builder ne crée, ne supprime et ne renomme aucune piste. Chaque piste demandée par le manifeste doit correspondre exactement à une piste visible existante. Les pistes du template qui ne sont pas utilisées restent présentes et vides. Le nombre de pistes du template et le nombre de pistes ciblées ne sont pas limités à une valeur métier particulière.

#### Manifeste ordonné et contrat des WAV

`clip_specs` est un itérable non vide de mappings. L'objet lui-même ne peut être une chaîne, un chemin, des octets ou un mapping unique. Chaque descripteur contient :

| Champ | Présence | Contrat |
|---|---|---|
| `audio_path` | requis | Chemin vers le WAV source. |
| `track_name` | requis | Nom exact, UTF-8, non vide et sans NUL d'une piste visible du template. |
| `physical_filename` | optionnel | Basename `.wav` utilisé dans `Audio Files` et les catalogues PTX; par défaut, basename de `audio_path`. Aucun chemin n'est permis. |
| `clip_name` | optionnel | Nom UTF-8 dans la Clip List; par défaut, stem de `physical_filename` suivi de `.A1`. |
| `placement_start_samples` | optionnel | Timestamp absolu UInt64 du placement; par défaut, référence temporelle BWF du WAV. Les booléens ne sont pas des entiers acceptables. |

L'ordre d'itération est conservé exactement dans le manifeste retourné, les catalogues média, la Clip List et l'ajout des événements. Les basenames physiques et les noms de clips doivent être uniques sans tenir compte de la casse. Une application cliente peut donc choisir librement son tri, ses regroupements et sa stratégie d'affectation des pistes avant l'appel.

Chaque source doit être un RIFF/WAVE little-endian. Le format audio accepté est strictement WAVE_EXTENSIBLE `0xFFFE`, sous-format IEEE float `00000003-0000-0010-8000-00AA00389B71`, mono, 48 kHz, 32 bits, `byte_rate=192 000`, `block_align=4` et `valid_bits=32`. Un seul `fmt ` d'au moins 40 octets, un seul `fact` d'au moins 4 octets, un seul `data` non vide et un seul `bext` d'au moins 412 octets sont requis. Le compteur UInt32 de `fact` doit égaler `data_size / 4`; cette durée doit tenir dans la largeur sélectionnée par le profil (UInt24 ou UInt32). La référence temporelle UInt64 à `bext+338` doit tenir sur UInt32. Les 32 premiers octets du basic UMID à `bext+348` ne peuvent être tous nuls.

Des événements d'une même piste peuvent se chevaucher. Chaque nouveau `0x1050` est ajouté après ceux déjà créés sur cette piste, dans l'ordre des descripteurs; un événement ultérieur est donc le dernier dans la priorité de spotting. Aucun trim, crossfade, mixage, déplacement ou changement automatique de piste n'est appliqué. Le format créé est un `0x1050` de `block_type=0x03`, contenant un `0x104f` de 35 octets (`block_type=0x0a`), ID UInt32 à `+2`, timestamp UInt64 à `+7`, type `0x03` à `+15`, constante `FE FF 00 00 00 00` à `+16`, huit `FF` à `+22`, puis la queue secondaire `00 01 01`. Lors du premier placement, le trailer brut `01 00` encore concaténé au header de la playlist vide est séparé et replacé après les événements comme dans la référence native.

`placement_start_samples` ne réécrit pas l'identité temporelle du média. La référence BWF demeure encodée dans `0x2106` et dans le parent `0x2628`; seul le timestamp UInt64 du placement `0x104f` reçoit la valeur explicite. Ainsi, un même fichier garde son identité BWF même lorsqu'une application choisit de le placer ailleurs dans la timeline.

#### Mutation des catalogues et identités

Le média et la définition prototypes sont réutilisés pour le premier fichier; les suivants sont clonés avec tous leurs `original_offset` effacés. Pour l'ordinal zéro-based `i` :

- le `0x1003` reçoit l'ordinal one-based `i+1`; son `0x1001+6` reçoit la durée dans la largeur (UInt24 ou UInt32) du profil;
- le header `0x2106` reçoit un FILETIME courant à `+29`, la référence temporelle BWF UInt32, un second FILETIME une seconde plus tôt et un UUID aux offsets du profil; la queue 58 octets reçoit toujours le basic UMID à `+18..+49`;
- la queue `0x2106+18..+49` reçoit les 32 octets du basic UMID BWF;
- le `0x2629` reçoit l'ID `i`, un nouvel UUID à `identity+23..+38`, l'index média UInt32 `i` à `media_link+96`, le `clip_name`, la durée et la ou les références temporelles aux offsets du profil;
- les enregistrements fixes `identity` et `media_link` peuvent avoir été scindés par un faux bloc vide fortuit. Ils sont réassemblés puis normalisés à exactement 48 et 104 octets. Le span `media_link`, situé après l'identité, doit être remplacé en premier; normaliser d'abord l'identité déplacerait les indices d'items et pourrait concaténer l'ancien et le nouveau lien en un record invalide de 208 octets;
- les compteurs `0x1004` et `0x262a` deviennent `N`; les `N` entrées sont contiguës et dans l'ordre du manifeste;
- tous les filename records `0x103a` sont reconstruits avec les `physical_filename` dans l'ordre du manifeste, en conservant le suffixe du prototype. Pour `N` médias et `K` nœuds, les compteurs sont recalculés directement à `N+K+2`, `N+K+1` et `N+1..N+K`;
- chaque playlist ciblée reçoit ses événements dans l'ordre relatif du manifeste. Aucun enregistrement `0x0002` n'est ajouté pour les nouveaux blocs, conformément aux comparaisons natives.

#### Transaction de fichiers et retour

Le dossier parent de `output_session_directory` doit exister et le dossier cible doit être absent, y compris sous forme de lien cassé. Le nom de session optionnel est un basename sans séparateur ni NUL; l'extension `.ptx` est ajoutée si nécessaire. Sans nom explicite, le basename du dossier cible est utilisé.

La fonction valide toutes les sources, tous les descripteurs et le modèle, puis mute le modèle en mémoire avant de créer un dossier temporaire frère. Elle y crée `Audio Files`, copie chaque WAV avec `shutil.copyfile()` sans modifier un seul octet, sauvegarde le PTX, le recharge, puis vérifie les pistes ciblées, le catalogue, la Clip List, les placements, les records fixes 48/104 octets, les IDs de définition et chaque index média UInt32. Le dossier complet est publié par un seul `os.replace()` vers une destination encore absente. Une erreur supprime seulement le dossier temporaire exact; aucun fichier existant n'est écrasé.

Le builder ne génère ni `DGDA`, ni `minf`, ni `regn`. Les sources doivent déjà fournir le BWF/UMID requis; les comparaisons natives disponibles montrent que Pro Tools préserve les chunks et échantillons d'origine avant d'ajouter ces trois chunks. La validation automatisée garantit que les WAV copiés sont byte-for-byte identiques aux sources. La sortie corrigée à deux médias a été ouverte, lue, sauvegardée et rouverte avec succès dans Pro Tools. Le cycle natif a conservé deux entrées physiques distinctes, leurs longueurs, les records fixes 48/104 octets, les IDs/index `0`/`1`, les timestamps, l'overlap d'un échantillon et les hashes des WAV.

Le retour est un dictionnaire compatible JSON : `session_path`, `audio_files_directory`, `track_count`, la liste `tracks` des noms distincts ciblés dans l'ordre de première utilisation et la liste `clips`. Chaque clip donne son ordre, ses chemins source/destination, son filename physique, son nom de Clip List, sa piste, sa référence BWF, son début de placement, sa longueur et sa fin en échantillons.

## 7. Fondus et crossfades

### 7.1 Dictionnaire `0x2630`

Une opération de fondu exige exactement une racine `0x2630`. Son premier segment commence par un compteur UInt32 égal au nombre de `0x262f` directs, chacun possédant un payload brut.

Chaque événement Fade (`0x104f[15] = 0x01`) utilise son UInt32 à `+2` comme index ordinal de `0x262f`. Le nombre d'événements Fade doit égaler le nombre de géométries; chaque index doit être valide et utilisé une seule fois.

| Géométrie | Taille | Champs lus/écrits |
|---|---:|---|
| Fade In | 22 | Discriminateur `0x20` à `+5`; longueur UInt16 à `+8`; mode créé `0x03` à `+10`; forme à `+11`. L'ancre de l'événement est le début du fade et doit correspondre au début d'un placement audio unique. |
| Fade Out | 26 ou 27 en lecture; 27 en création | Discriminateur créé `0x22` à `+5`; longueur UInt16 à `+8`; la création la répète à `+10`, écrit le mode `0x02` à `+12` et la forme à `+13`. L'ancre est la fin du fade et doit correspondre à la fin d'un placement unique. |
| Fade Out UInt24 (non publié, lecture seule) | 29 | Discriminateur requis `0x33` à `+5`; longueur UInt24 LE à `+8`, répétée à `+11`; les deux valeurs doivent être égales et positives. Mode requis `0x02` à `+14`. Les autres octets, dont la forme à `+15`, sont opaques et préservés. L'ancre est la fin du fade, associée à la fin d'un placement audio unique. |
| Crossfade | 34 | Discriminateur créé `0x22` à `+5`; pré-roll UInt16 à `+8`; durée totale UInt16 à `+10`; mode `0x01` à `+12`; Equal Power `0x01` à `+13`. L'ancre est le cut. |

Une autre longueur de payload est rejetée. Pour la lecture d'un crossfade, l'audio droit commençant à l'ancre est préféré; l'audio gauche finissant à l'ancre sert de repli. L'association doit rester unique.

Le chemin local non publié `_decode_fade_out_length()` conserve le décodage historique UInt16 pour 26/27 octets et lit uniquement le nouveau profil vérifié 29/`0x33`/`0x02`. Un profil de 29 octets à largeur ou mode différent, deux durées divergentes ou une durée nulle est rejeté sans mutation. Le début calculé `anchor - duration` ne peut pas être négatif. Aucune déduction générique depuis les nibbles ni fallback par longueur n'est ajouté pour les autres tailles; le fade-out compact de 25 octets décrit dans la contribution reste non vérifié et refusé.

Deux sessions natives Pro Tools 2024.3.1 confirment ce profil à 48 kHz. À 25 fps, le clip audio s'étend de `1728000000` à `1728192192` (192192 échantillons), le fade-out de `1728096000` à `1728192192` (96192 échantillons). À 23.976, le clip s'étend de `1729728000` à `1729920192` (même longueur), le fade-out de `1729824096` à `1729920192` (96096 échantillons). Les TC arrondis des fades sont, dans les deux sessions, `10:00:02:00 → 10:00:04:00`. Le lecteur retourne les valeurs exactes du PTX, pas une durée supposée de 96000 échantillons depuis le label « 2 secondes ». Les comparaisons avant/après à 25 fps confirment l'absence de changement de la définition et du placement audio. Les copies API sans mutation sont byte-identiques; leur ouverture, sauvegarde et réouverture dans Pro Tools ont été confirmées aux deux cadences. Les fichiers sauvegardés par Pro Tools conservent les positions et durées exactes; deux cycles API sans mutation sont ensuite byte-identiques pour chaque fichier. Les quatre cas natifs (avant et après sauvegarde Pro Tools, aux deux cadences) sont couverts par les tests optionnels locaux.

La reconnaissance d'un fade-out attaché inclut aussi la taille 29 pour les garde-fous de move/duplicate/split/trims et le refus d'un fade-out déjà présent à la même ancre. Elle n'autorise aucune modification de ce fondu. `add_fade()` et `add_crossfade()` restent limités à leurs profils UInt16 historiques, même si le lecteur peut maintenant lire ce fade-out natif UInt24.

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

### 8.1 Catalogue des règles de markers et filtre de lecture

Le profil natif multi-règle vérifié contient une unique racine `0x2519`, un enfant direct `0x251b`, puis des entrées directes `0x251c`. Une entrée de règle a le payload exact suivant :

```text
UInt16LE ruler_id
UInt32LE name_length
UTF-8 name[name_length]
0x01
UInt32LE ruler_id
```

Les entrées `0x251c` qui n’ont pas cette forme sont des entrées UI non-règle et sont ignorées. Les `ruler_id` et noms de règle doivent être uniques. Le dernier segment brut de huit octets d’un événement marker `0x2077` est `UInt32LE(0) | UInt32LE(ruler_id)`.

`get_markers(marker_track_name=None)` reste entièrement en lecture seule. Sans argument, il retourne tous les marqueurs reconnus. Avec un nom de règle, il valide le catalogue ci-dessus et l’assignation de **chaque** marker, puis retourne uniquement ceux dont l’ordinal final correspond au nom demandé. Un catalogue absent, ambigu, invalide, un nom inconnu ou une assignation inconnue est refusé par `ValueError`; aucun repli par position ou par nom de marker n’est autorisé.

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

### 9.1 Profil historique entièrement statique

Le writer exige une racine `0x2637` plate unique; dans le profil entièrement statique :

```text
point_count UInt32 LE | point 0 (30 octets) | ... | point N-1
```

Chaque point contient les 26 octets de métadonnées vérifiés ci-dessous et une valeur Float32 LE finie aux octets `26..29`. La taille du payload entièrement statique est exactement `4 + 30 × point_count`. Le nouveau validateur `_validated_clip_gain_dictionary()` vérifie chaque record, pas seulement cette taille totale.

Dans chaque `0x2628`, l'index signé Int32 à `len(payload)-6` vaut `-1` sans Clip Gain ou référence un point existant. Tous les index de toutes les définitions sont validés avant écriture.

`set_clip_gain()` :

- Cible une définition de clip au nom unique; toutes ses occurrences partagent donc le gain statique.
- Accepte, sauf les booléens, toute valeur convertible par `float()` vers un réel fini, ainsi que `-math.inf`, `"-inf"` ou `"-infinity"`; `NaN` et `+inf` sont rejetés.
- Remplace `-inf` par la sentinelle Pro Tools Float32 `-290.2105712890625`, octets LE `f4 1a 91 c3`.
- Si l'index vaut `-1`, ajoute un point avec les 26 octets de métadonnées `01 46 01 00 16 00 00 00 00 00 01 00 00 00 04 00 00 00 00 00 00 00 00 00 00 00`.
- Si plusieurs définitions partagent le point, clone les 30 octets et relie uniquement la cible au nouvel index.
- Sinon, remplace uniquement les quatre octets Float32 du point existant.
- Le retour est l'index du point global finalement lié à la définition.

Le header statique est exactement la séquence de 26 octets de création ci-dessus; il porte magic `01 46 01 00`, taille UInt32 0x16, padding nul, un nœud, flags `04 00`, zéro segment, réservé nul et position relative zéro. Headers non vérifiés et anciennes valeurs non finies sont désormais refusés avant mutation, y compris dans un autre record non ciblé.

Séquences Float32 LE de référence :

| Valeur | Octets LE | UInt32 équivalent |
|---:|---|---:|
| `0.0` | `00 00 00 00` | `0x00000000` |
| `-10.0` | `00 00 20 c1` | `0xc1200000` |
| `+6.0` | `00 00 c0 40` | `0x40c00000` |
| sentinelle `-inf` | `f4 1a 91 c3` | `0xc3911af4` |

L'édition des enveloppes/breakpoints de Clip Gain n'est pas prise en charge.

### 9.2 Dictionnaire mixte — modification non partagée validée, non publiée

La paire native statique + enveloppe vérifie un dictionnaire brut de 148 octets : count UInt32 = 4, trois records statiques de 30 octets et un record d'enveloppe de 54 octets. Pro Tools réordonne ces quatre entrées entre before et after et réassigne les index des définitions; les enregistrements associés aux mêmes noms de clips sont inchangés, excepté MEDIA-01 passant +3→+6 dB. L'API conserve l'ordre et les index existants au lieu de copier cette réorganisation.

Chaque record commence par magic `01 46 01 00` puis taille UInt32 LE à +4. Sa longueur totale est **8 + taille déclarée**, contrairement au payload Volume de §10. Le parseur commence à +4 du dictionnaire, avance strictement par ces tailles, lit exactement count records et exige la fin exacte du payload. Aucun padding, aucun scan/find de signature et aucun recours à un stride de 30 sur un dictionnaire mixte. Une signature présente dans une valeur de nœud ne crée pas de record.

| Champ dans un record | Statique vérifié | Enveloppe conservable vérifiée |
|---|---:|---:|
| Taille totale / UInt32 à +4 | 30 / 0x16 | 54 / 0x2e |
| Padding +8..+9 | zéro | zéro |
| Nombre de nœuds UInt32 +10 | 1 | 4 |
| Flags UInt16 +14 | 4 | 4 |
| Segments UInt32 +16 | 0 | 3 |
| Réservé UInt16 +20 | zéro | zéro |
| Paires à +22, chacune 8 octets | position UInt32 0, gain Float32 | quatre positions UInt32/gains Float32 |

L'enveloppe exige une première position relative zéro, des positions strictement croissantes et des valeurs finies. Aucun terminateur après les paires. Son header exact de 22 octets est `01 46 01 00 2e 00 00 00 00 00 04 00 00 00 04 00 03 00 00 00 00 00`. Les tailles inconnues, notamment 0x26 annoncée dans la contribution mais non observée dans cette paire, sont refusées par `NotImplementedError`; les incohérences/troncatures/valeurs non finies par `ValueError`.

`set_clip_gain()` valide tous les records et tous les index de toutes les définitions avant la mutation. Dans le cas d'un point statique existant non partagé, il remplace sa valeur Float32 à `record_offset+26` et conserve count, ordre, autres records et tous les index. La cible d'enveloppe est refusée sans mutation. Le cas partagé fait l'objet de §9.3, l'ajout d'un premier point (index -1) de §9.4. Le profil entièrement statique de 9.1 conserve ajout/clonage; aucune normalisation vers un autre profil.

Preuve native : courbe B aux positions relatives `0, 96096, 384384, 576576`, soit départ B puis TC absolus 10:00:12:00/18:00/22:00 dans cette session 48 kHz/23.976. Les valeurs natives sont `0, 0, environ -5.952, environ -0.000006 dB`; l'API ne les arrondit pas vers les valeurs nominales demandées. Le record entier est byte-identique avant/après, même lorsque Pro Tools change son ordinal.

Les seize premières régressions ont porté la suite à 402 tests. La sortie API ne différait du before que d'un octet chiffré et conservait 172 records/cibles logiques et tous les médias/placements. **Ouverture/lecture/sauvegarde/réouverture dans Pro Tools confirmées le 7 octobre 2026**. La sauvegarde native conserve les quatre gains par nom (dont +6 dB cible et courbe exacte), Clip List/placements/pans/routes/Volume et les mêmes cibles de pointeurs. Ses métadonnées opaques/path de médias peuvent évoluer nativement; elles ne sont pas normalisées par l'API. Deux cycles API et compositions +6 no-op/+3/+6 restituent le fichier entier byte-identique. Quatrième fixture/régression native locale ajoutée. Profils UUID modernes et édition d'enveloppe hors portée.

### 9.3 Clonage d'un point partagé mixte — validé, non publié

Le contrôle de partage porte sur **toutes les définitions de clips**, pas seulement les occurrences visibles. Si plusieurs définitions référencent le même point statique existant, le chemin de clonage de 9.1 est réutilisé :

1. Copier le record statique complet de 30 octets depuis l'offset borné de §9.2 (pas `4 + 30 × index` dans un dictionnaire mixte).
2. Remplacer uniquement son Float32 +26..+29 dans la copie.
3. Ajouter cette copie à la fin du dictionnaire et incrémenter le count UInt32.
4. Relier seulement la définition cible au nouvel ordinal (ancien count), dans son index Int32 historique à -6.

Ancien point, ordre/bytes de tous les records existants, index de toutes les autres définitions et enveloppe restent intacts. Prévalidation/préparation complète avant affectations. L'index ajouté est borné par Int32 signé comme dans 9.1. Une cible devenue non partagée est ensuite modifiée en place, sans re-clonage ni accroissement du count. Aucun nouvel enfant PTBlock/pointeur individuel pour le record, puisque `0x2637` reste un payload brut; la sauvegarde relocalise les pointeurs standards après la croissance du bloc.

Le partage de validation est **construit de façon contrôlée sur la référence native resauvegardée de 9.2**, pas observé dans une nouvelle paire native : l'entrée parent MEDIA de Clip List (non placée) est reliée au point +6 de MEDIA-01. Ce changement d'index connu ne change qu'un octet de la base. Le writer change alors seulement MEDIA-01 vers +3 par clonage; MEDIA reste à +6 et la courbe de MEDIA_01-01 reste active et exacte. Count 4→5, payload 148→178; index cible 4, ancien index partagé 2. Les 172 records/cibles logiques, pistes, placements et WAV sont conservés.

**Ouverture/lecture/sauvegarde/réouverture Pro Tools confirmées**. La sauvegarde native compacte le dictionnaire à 4 records/148 octets, enlève l'ancien record inutilisé et relie la cible à l'index 3; MEDIA reste +6/index 2 et la courbe reste byte-identique/index 0. Ce compactage n'est pas une règle d'écriture API : contrôler les gains par nom de définition, pas par ancien ordinal. Clip List/placements/172 records-cibles/Volume/pans/routes/WAV conservés; deux cycles et modifications indépendantes +5/-10 puis +6/+3 restituent le fichier entier. Fixture/régression de save natif ajoutée. Les premiers 21 tests mixtes portaient la suite à 407 tests; la suite courante et l'ajout de premier gain sont décrits en 9.4. Autres enveloppes, édition de courbe et profils UUID modernes restent hors portée.

### 9.4 Premier point statique mixte — sortie Pro Tools à valider

Pour une cible d'index historique -1 dans un dictionnaire mixte vérifié, le writer réutilise l'ajout de 9.1 : créer le point statique de 30 octets avec ses 26 octets de métadonnées vérifiées et la valeur Float32 validée; l'ajouter à la fin, incrémenter le count et relier seulement la cible à l'ancien count. Toutes les définitions et tous les records sont prévalidés, y compris les entrées non ciblées. Aucun réordonnancement ou retrait de record, aucun effet sur l'enveloppe.

Plusieurs index -1 ne représentent pas un partage de point : la branche d'ajout prime sur le comptage de références identiques, et les autres -1 restent inchangés. Une seconde attribution à la même cible modifie son nouveau point en place, sans allocation supplémentaire. Limite Int32 signé inchangée. Les cibles d'enveloppe restent refusées, de même que les tailles de records non vérifiées; aucune édition de breakpoints ni détection UUID nouvelle.

Test **construit**, pas nouvelle paire native : base resauvegardée validée de 9.3; seul l'index de MEDIA-01 passe de 3 à -1 (quatre octets changés), dictionnaire inchangé. L'API attribue -3 dB : nouvel index 4, count 4→5, payload 148→178; témoin MEDIA +6 et courbe de B préservés. Ancien record +3 devenu inutilisé conservé, pas normalisé. Deux cycles exacts, mises à jour +6/-10/-3 sans augmentation de count et 172 records/cibles logiques inchangés. **Validation manuelle de cette sortie encore attendue; ne pas publier cet ajout mixte avant confirmation.**

Suite actuelle : **412 tests avec -W error**, AST Python 3.8 valide. 26 tests mixtes : 19 synthétiques, 5 natifs optionnels, 2 sur bases natives à conditions construites (partage/absence). Nouveaux contrôles de la sauvegarde compactée de 9.3, ajout initial, plusieurs -1 indépendants, sentinelle -inf et réutilisation du point, refus tardifs de record/enveloppe/index sans ajout partiel, cycles/positions/pointeurs et compositions exacts.

## 10. Automation de volume

La cible du writer historique `add_volume_node()` est une définition `0x261c` :

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

### 10.1 Lecture seule du profil mono nommé (`get_volume_automation`, non publié)

`get_volume_automation(track_name=None)` lit les enveloppes de Volume sans aucune mutation. L'argument facultatif doit être `None` ou un `str` non vide, sans NUL, UTF-8 valide; le nom est exact et sensible à la casse. Sans playlist principale, l'appel non filtré retourne `[]`; une demande nommée inexistante est refusée. Les playlists principales doivent être nommées : les profils anonymes ne sont pas élargis ici. `_validated_anonymous_mono_track_names(N)` est réutilisé pour corroborer noms, ordre, compteurs et identités Audio mono via `0x1015`/`0x2107`/les deux familles `0x251a`/`0x261c`, pas pour autoriser les playlists anonymes. Les noms doivent égaler ceux de la map principale. Aucun fallback par nom interne, nombre seul ou anciens offsets de `self.data`.

`_validated_mono_volume_lanes()` exige une racine `0x2624` unique/cohérente selon ce validateur, aucun slot `0x261c` imbriqué; chaque slot est type `0x04`, sa définition directe unique `0x261b` type `0x0d`. Le conteneur principal `0x260d` est unique directement et dans tous les descendants du slot, type `0x05`, avec **exactement 13 items** :

```text
0  [0x1029/type0x0d, contenu opaque]
1  brut 01 00
2  [0x260e/type0x09, contenu opaque pour ce lecteur]
3  brut 01
4  [0x260a/type0x01 : Volume]
5  brut 01
6  [0x260a/type0x01 : autre automation, non interprétée]
7  brut 00
8  [0x260c/type0x02 : pan, contenu opaque pour ce lecteur]
9  [0x260c/type0x02 : deuxième pan, contenu opaque pour ce lecteur]
10 brut 01
11 [0x260a/type0x01 : autre automation, non interprétée]
12 brut 00 × 8
```

La paire native identifie l'item 4 comme Volume : lui seul change quand le point est ajouté dans la vue Volume. Une recherche de magic/signature ou la lecture de tous les `0x260a` serait incorrecte; les items 6/11 et les pans imbriqués ne sont pas des résultats Volume. Leur contenu reste opaque; ce lecteur ne valide pas les routages ni le pan lui-même. Un autre ordre/type/sélecteur, une géométrie supplémentaire/manquante, un conteneur de state ambigu ou des identités contradictoires sont refusés.

La lane de Volume doit posséder un seul payload brut. `_decode_verified_mono_volume_payload()` exige au moins 30 octets, identifiant `01 46 01 00`, padding `+8..9` et terminateur nuls, `N >= 1`, longueur `24 + 6*N`, taille `+4 == len(payload)-10`, flags `+14..15 == 02 00`, réservé `+20..21 == 00 00`, segments `+16 == N-1`, timestamps UInt32 strictement croissants/uniques. Chaque point lu est `[UInt32LE sample][Int16LE valeur]`; `db = valeur / 10.0`, timecode calculé par `TimecodeEngine` courant. Toutes les lanes Volume du profil sont validées **avant** le filtre et le retour; aucune enveloppe tronquée/partielle ni `ok=False` de récupération n'est retournée.

Résultat frais, une entrée par piste lue dans l'ordre des slots : `{"track": nom, "ordinal": index_zero_based, "ok": True, "reason": "", "node_count": N, "payload_len": taille, "nodes": [{"sample": UInt32, "timecode": label, "db": valeur/10.0}, ...]}`. Un filtre retourne seulement l'entrée exacte, en conservant son ordinal d'origine. Listes/dictionnaires n'exposent aucun objet mutable de l'arbre. Le profil de lecture stéréo est ajouté séparément en 10.3; folders, variantes anonymes/cachées/réordonnées ou layouts alternatifs restent hors portée. Le writer mono non publié est décrit en 10.2 et n'est pas élargi par 10.3.

**Preuve native** : à 48 kHz/enum `0x09`, before : `(0, 0)`; after pour la piste ciblée : `(0, 0)` puis `(1730208480, 60)`, soit `10:00:10:00 / +6 dB`. Payload `30 → 36`, taille `20 → 26`, compteur `1 → 2`, segments `0 → 1`; autres lanes/pans/routes/catalogues/clips/WAV et 172 records/cibles logiques inchangés. Deux octets de `0x1029` changent nativement à `+1` et `+87` de `0` à `60`. Leur rôle n'est pas établi : aucun cache/fader/mirror n'est normalisé ou copié par supposition. Douze régressions dont trois natives optionnelles vérifient lecture exacte, refus sans mutation et deux cycles de sauvegarde/relecture byte-identiques par original; le writer historique reproduit le seul payload de Volume sans copier `0x1029`, sans certification du writer bulk. **344 tests réussis avec `-W error`, syntaxe Python 3.8 vérifiée; lecture validée sur cette paire native.** Aucune nouvelle écriture n'est intégrée dans cette étape.

### 10.2 Remplacement/fusion — profil mono validé (`set_volume_automation`, non publié)

Signature : `set_volume_automation(track_name, nodes, replace=True, ordinal=None)`. Un seul nom exact de piste Audio mono du profil 10.1; `track_name` est une chaîne non vide, sans NUL, UTF-8 valide. `replace` est strictement booléen. L'ordinal facultatif est un entier non booléen >= 0 qui doit correspondre au slot live de cette piste; il ne permet aucun fallback de résolution.

`nodes` est un itérable de paires `(sample, db)`; chaînes/octets refusés comme paire. Samples entiers non booléens dans `0..0xffffffff`; ordre d'entrée libre, doublons entrants refusés. dB non booléen convertible par `float()`, fini; `round(db * 10)` doit rester fini et dans `-32768..32767`. L'arrondi est celui de Python (égalité vers l'entier pair), comme le writer historique. `replace=True` remplace tous les points et exige au moins un point. `False` fusionne avec l'enveloppe live : point entrant prioritaire seulement à timestamp égal, autres anciens points conservés. Fusion vide permise; résultat trié strictement par sample. Aucun effacement complet de l'enveloppe.

Validation de **toutes** les enveloppes existantes par `_validated_mono_volume_lanes()` avant traitement des entrées, même pour remplacement/no-op. Un compteur incohérent dans une autre piste n'est ni ignoré ni normalisé. Tous les paramètres, points et allocations du nouveau payload/résultat précèdent la seule mutation. Une exception de l'itérateur utilisateur est propagée sans modifier l'arbre. Aucun rollback partiel ou écriture anticipée d'une première lane.

Écriture du seul payload direct de Volume décrit en 10.1 : magic conservé `[:4]`; taille UInt32 `14 + 6*N` à +4; padding `[8:10]` conservé; compteur UInt32 N à +10; flags `[14:16]` conservés; segments UInt32 `N-1` à +16; réservé `[20:22]` conservé; points `<Ih` à +22; terminateur original de deux octets. Longueur `24 + 6*N`. Limite `N <= (0xffffffff - 26)//6`, tenant compte des deux octets content_type du bloc PTX; sinon `OverflowError`. Nouveau payload redécodé/validé avant affectation. Enveloppe identique : aucun remplacement d'objet payload. Objet lane, original_offset, autres automations, pans, routage, catalogues, `0x1029` et métadonnées de suppression restent intacts. Retour préalloué `[(ordinal, N)]`, y compris no-op. Pas de save implicite.

Preuves : 15 régressions (11 synthétiques/4 natives optionnelles); le remplacement par `(0, 0 dB), (1730208480, +6 dB)` et la fusion du seul second point reproduisent exactement le payload natif et donnent des PTX bit-identiques. Deux cycles de sauvegarde/relecture exacts et retour à `(0, 0 dB)` restaurant intégralement le before. Refus tardifs/types/limites/générateur interrompu sans mutation vérifiés. **359 tests avec -W error**, AST Python 3.8 valide. **Ouverture/sauvegarde/réouverture du PTX écrit confirmées dans Pro Tools le 6 octobre 2026**. La sauvegarde native conserve ces points, les autres lanes/pans/routes/clips et les 172 records/cibles logiques; deux cycles API et compositions remplacement/fusion restituent le fichier resauvegardé bit pour bit. Les différences natives opaques `0x1029` à +1/+87 ne sont pas copiées : Pro Tools les modifie comme dans l'after natif, sans établir leur rôle; aucune hypothèse de cache/fader ni synchronisation ajoutée. Étape mono clôturée, non publiée; pistes stéréo/multi-lanes et autres profils hors portée. `add_volume_node()` inchangé.

### 10.3 Lecture du Volume Audio stéréo (`get_volume_automation`, non publié)

La paire native mono–stéréo–mono confirme **trois pistes/slots `0x261c`, quatre playlists principales `0x1052`, trois enveloppes de Volume**. La stéréo utilise deux playlists de même nom (canaux L/R), mais une seule lane Volume dans son état principal. Il est incorrect de nommer la troisième lane selon la troisième playlist : cette dernière est encore le canal droit de la stéréo. Le résultat public conserve le format 10.1 et l'ordinal de slot (0,1,2), sans entrée supplémentaire par canal.

Le getter appelle `_validated_named_audio_volume_lanes()`, puis `_validated_audio_track_identities(count, allow_stereo=True)` avec le compte des descripteurs directs `0x1014`. Les racines/compteurs/descripteurs, metadata `0x210b`, deux familles `0x251a` et identité native de `0x2619` sont corroborés comme en 10.1. Le wrapper `_validated_anonymous_mono_track_names()` utilise ce même validateur avec `allow_stereo=False` : aucun support stéréo/anonyme ni writer supplémentaire n'est introduit par ce factoring.

Après chaque nom préfixé `0x1014`, mono : queue 39 octets, préfixe `00 01 00 00 00`, marqueur `2a 00 00 00` à +11, deux index UInt32 à +5/+30 égaux au **premier index de playlist de canal**. Stéréo : queue 45 octets, préfixe `01 02 00 00 00`, deux index UInt16 +5/+7 égaux à `k,k+1`, padding nul +9..12, marqueur à +13, deux index UInt32 +32/+36 égaux à `k,k+1`. Donc index de canal stéréo représentable en UInt16; les autres champs de cette queue restent opaques, aucune signification déduite pour les GUID/champs non corroborés. `k` avance de 1 (mono) ou 2 (stéréo), pas toujours d'une piste. Toute autre géométrie/largeur ou index incompatible est refusé.

Dans chaque queue de nom `0x251a` de 42 octets, les six premiers octets doivent être `[width-1] + cinq zéros` et les deux derniers `[width-1, 0]`; les deux familles doivent corroborer le nom, l'identité et l'ordre déjà exigés en mono. Le préfixe avant le nom reste `00 00`. Une largeur de miroir seule ne suffit pas : elle doit concorder avec les deux formes de descripteur ci-dessus. En session contenant une stéréo, chaque slot a un unique record brut de 12 octets `[UInt32 ordinal][01 00][UInt16 ordinal][00 00 ff ff]` cohérent avec son rang. Cette vérification ne passe pas par un nom interne périmé ou un simple compte global.

La séquence des noms de pistes est développée selon leur largeur, puis comparée exactement à **toute** la map principale nommée. Identités uniques et noms de pistes uniques obligatoires; le nom répété dans les deux playlists d'une même stéréo n'est pas une piste homonyme. `_validated_volume_state_lanes(names)` partage la géométrie stricte de 13 items et le décodeur d'enveloppe de 10.1. Ce dernier garde son nom privé `_decode_verified_mono_volume_payload` : le payload stéréo observé est identique au profil mono, pas un nouveau format supposé. Tous les états/enveloppes sont validés avant filtre/retour; seules la lecture live et des listes/dictionnaires frais sont utilisés.

Preuves natives : le Volume de la piste stéréo seul passe de 30 à 36 octets, `(0,0)` puis `(1730208480,60)` à 48 kHz/enum `0x09`. Les monos, pans L/R, routage, autres lanes, Clip List, placements et WAV sont inchangés; les **179 records/cibles logiques** restent les mêmes. Opaque `0x1029` : seules positions +1/+87 changent de 0 à 60 sur la stéréo, sans interprétation/synchronisation API. Treize tests (10 synthétiques/3 natifs optionnels), dont plusieurs positions/stéréos adjacentes synthétiques; deux cycles API par original byte-identiques. **372 tests avec -W error**, AST Python 3.8 valide. Lecture clôturée sur la paire native produite dans Pro Tools; aucun nouvel authored PTX à valider pour ce lecteur.

**Séparation lecture/écriture** : lors de l'étape 17, `set_volume_automation()` restait mono strict. L'extension d'écriture stéréo/mixte est décrite en 10.4 avec son statut de validation distinct. `add_volume_node()`, `get_tracks()`, lecteurs de timeline, resolver anonyme et routing ne changent pas de contrat. Aucun lecteur Pan/gain, piste non Audio, largeur >2, profil caché/réordonné/anonyme ou multi-lane alternatif revendiqué.

### 10.4 Remplacement/fusion stéréo/mixte — validé dans Pro Tools, non publié

Même méthode publique/signature/règles de points que 10.2. Seul le resolver cible change : `set_volume_automation()` appelle désormais `_validated_named_audio_volume_lanes()` décrit en 10.3, pas `_validated_mono_volume_lanes()` directement. Toutes les identités/largeurs/index de playlists/ordinaux de slots, tous les états et toutes les enveloppes existantes doivent être valides **avant** les entrées et la seule affectation au payload cible. Profil stéréo inconnu ou enveloppe invalide d'une autre piste refusés même en remplacement complet. Aucun fallback par compteur, GUID périmé ou anciens offsets.

Un nom unique de piste cible **une seule lane Volume**, y compris en stéréo : aucun clone/écriture séparée de canaux L/R. `ordinal` facultatif est l'index live du slot de piste; dans mono–stéréo–mono, la dernière piste a ordinal 2 et non l'index 3 de sa playlist de canal. Retour `[(ordinal, N)]`. Le payload observé et les règles de tri/UInt32/Int16/arrondi/doublons/remplacement non vide/fusion/no-op restent ceux de 10.2. Aucune reconstruction d'état/pan/routage, nouvel offset ou métadonnée de suppression; les flags/padding vérifiés et `0x1029` restent ceux du before. Le rôle de ses deux changements natifs n'est pas déduit.

Quatorze régressions (10 synthétiques/4 natives optionnelles) : payload cible exactement égal au Volume du after natif; remplacement par `(0,0 dB),(1730208480,+6 dB)` et fusion du second point produisent des PTX bit-identiques. Deux cycles API exacts, retrait du point restaurant tout le before, compositions successives mono–stéréo–mono et cible mono après stéréo vérifiés. Tests de limites/types/arrondi/no-op, pans L/R et autres lanes conservés, input tardif/générateur interrompu, identités/width/ordinal/state/enveloppe tardive invalides sans mutation. Deux anciens tests de refus d'écriture stéréo portent désormais sur une largeur corrompue. **386 tests avec -W error**, AST Python 3.8 valide; 836 raises inchangés.

**Validation Pro Tools et contrôle de la sauvegarde native reçus le 6 octobre 2026.** Points exacts, pans L/R, routes, autres lanes, Clip List/placements, WAV et 179 records/cibles logiques conservés. Pro Tools modifie les deux octets opaques `0x1029` comme dans l'after natif; aucun rôle de cache/fader ni synchronisation ajoutée. Deux cycles API et compositions remplacement/fusion, y compris modifications puis restauration des monos, restituent ce fichier resauvegardé bit pour bit. Étape 18 clôturée, extension non publiée; la validation mono de 10.2 demeure acquise. Aucun writer multi-cible/automation générale ni extension des autres API revendiqué.

## 11. Clip Groups

La lecture des définitions est indépendante des clips audio : `0x262c` contient un compteur UInt32 et des `0x262b`; l'ID de groupe est leur ordinal. Les macros de timeline utilisent ce namespace, pas celui de `0x262a`. Le lecteur `get_timeline_clip_groups()` accepte autant de définitions et de macros visibles que la session en contient. Les écrivains restent intentionnellement plus étroits : `delete_clip_group()` dissout le profil simple historique, `create_clip_group()` convertit une région audio native existante à partir d'un prototype de template, et `create_empty_clip_group()` écrit le profil natif vide décrit en 11.2.

### 11.1 Création template-driven (`create_clip_group`)

La signature publique est `create_clip_group(track_name, group_name, start_samples, prototype_group_name)`. Elle ne crée ni média ni définition audio : elle convertit **une** occurrence audio visible existante (`0x1050` / `0x104f`, type `0x03`, queue `00 01 01` ou `01 01 01`) sur la piste et au timestamp demandés. Sa longueur est celle de la définition audio ciblée. Les applications clientes restent donc responsables de fournir la région source dans une session compatible, sans convention de filename ou de commentaire imposée par l'API.

Le profil d'écriture validé est le groupe audio simple mono/composant suivant :

- Racines uniques `0x262c`, `0x2424`, `0x2426`, `0x2428` et timeline principale `0x1054`; leurs listes de groupes `0x262b`, noms `0x2423`, métadonnées `0x2425` et pistes internes `0x1052` ont toutes le même compteur UInt32.
- `prototype_group_name` désigne une définition existante et exactement une macro visible `00 00 01`; le prototype interne correspondant est une `0x1052` **vide** (`count=0`, queue `01 00`). Le groupe nouvellement produit a donc une seule piste et un seul événement interne.
- Le nouveau groupe reçoit l'ordinal `N` (ancien compteur). Il est ajouté aux quatre listes parallèles. Son nom UTF-8 doit être absent de toute la liste `0x262b`, pas seulement des macros déjà placées. Le trailer 9 octets de `0x262b` porte l'ID à l'octet 1; le profil observé limite donc la création à `N <= 255`.
- Le `0x2628` du prototype est cloné. Sa longueur complète est réécrite à `A+10`, avec sélecteur indépendant `A+2` (`0x10/0x20/0x30/0x40`) choisi selon la largeur minimale de la longueur cible. Les deux UInt32 qui suivent immédiatement cette longueur deviennent `start_samples`. Les octets inconnus sont conservés. Dans le profil validé, le UInt32 opaque à `A+6` est l'origine de coordonnées de la playlist interne; il est recopié comme UInt64 à `+7` de l'événement audio caché, et **n'est ni zéro ni le timestamp visible**.
- Le payload `0x2523` cloné reçoit le nouveau début à `+0`, le nouvel ID à `+16`, la fin `start + length` à `+44`, et sa valeur à `+36` est ajustée par la différence de longueur par rapport au prototype. Les autres octets restent ceux du prototype.
- Le `0x2423` cloné porte `[group_id UInt32][name_length UInt32][UTF-8][queue conservée]`; le `0x2425` parallèle porte le nouvel ID UInt32 à `+5`.
- L'événement audio source est déplacé (cloné puis remplacé) dans la nouvelle `0x1052` cachée avec queue audio conservée et timestamp interne décrit ci-dessus. Dans la piste visible, son clone devient la macro : ID `N` à `payload+2`, `start_samples` UInt64 à `payload+7`, flag obligatoire `payload+18 = 1`, queue `00 00 01`. Omettre ce flag produit un groupe présent dans la Clip List mais absent de la timeline.
- Trois enregistrements standard de `0x0002` (nom `0x2423`, métadonnée `0x2425`, piste cachée `0x1052`) sont ajoutés dans les séries respectives des trois prototypes. Les nouveaux blocs reçoivent des offsets synthétiques uniques seulement pour cette relocalisation; `save()` les remplace par leurs offsets sérialisés. Les définitions `0x262b` et événements `0x1050` ajoutés ne reçoivent pas d'enregistrement supplémentaire dans le profil observé.

Toutes les validations précèdent la mutation et l'arbre est restauré transactionnellement si une étape échoue. `start_samples` et `start_samples + length` doivent tenir dans UInt32, bien que la macro visible utilise un UInt64. Les groupes imbriqués, plusieurs composants/événements/pistes internes, fades internes, prototype placé plusieurs fois, région source absente ou ambiguë, et toute queue ou compteur non observé sont refusés.

La validation manuelle Pro Tools a confirmé des sorties API de durée fixe et variable, y compris sur des pistes distinctes.

### 11.2 Création de groupes vides (`create_empty_clip_group`)

La signature publique est `create_empty_clip_group(track_name, group_name, start_samples, length_samples)`. Elle n'exige ni région audio visible, ni média, ni prototype de groupe : elle crée directement un Audio Region Group vide de durée positive. Elle est réservée au profil natif explicitement observé dans une session vierge à 48 kHz / enum `0x09` (23.976). Toute autre fréquence ou cadence est rejetée avant mutation.

Le profil écrit est le suivant :

- Les racines uniques `0x262c`, `0x2424`, `0x2426`, `0x2428` et la timeline principale `0x1054` sont requises. Les listes parallèles `0x262b`, `0x2423`, `0x2425` et `0x2428 → 0x1054 → 0x1052` portent le même compteur UInt32. Elles peuvent être vides, ou ne contenir que des groupes déjà conformes à ce profil vide.
- Le nouvel ordinal est `N`, le compteur précédent. Le trailer de définition `0x262b` est `00 N 00 00 00 00 00 00 00`; le profil validé accepte les IDs `0..255`, soit au plus 256 groupes. Les noms UTF-8 sont globaux dans `0x262c` et doivent être uniques.
- Le payload `0x2628` commence par le nom, puis `00 50`, un sélecteur de largeur `0x10`, `0x20`, `0x30` ou `0x40`, `44 08 00`, l'origine opaque UInt32 LE `0xE8D4A510`, la durée little-endian de largeur sélectionnée, et deux UInt32 LE `start_samples`. Les deux champs temporels doivent tenir dans UInt32.
- Sous cette définition, `0x2523 → 0x2526` contient un payload de 65 octets. Il porte le début à `+0`, l'ID à `+16`, l'origine encodée sur cinq octets à `+29`, l'origine plus durée sur cinq octets à `+36`, et la fin à `+44`. Les autres octets sont fixes dans le profil vide observé.
- Le nouveau `0x2423` est `[group_id UInt32][name_length UInt32][nom UTF-8][cinq octets nuls]`. Le `0x2425` parallèle est un payload opaque de 103 octets du profil observé; seul son ordinal UInt32 LE à `+5` est écrit par l'API. La playlist interne `0x1052` est vide et a exactement le header `01 00 00 00 3F 00 00 00 00 01 00`.
- La macro visible est `0x1050 → 0x104f` de 35 octets, `group_id` à `+2`, `start_samples` UInt64 LE à `+7`, type `0x03` à `+15`, flag `0x01` à `+18`, et queue secondaire `00 00 01`. Trois enregistrements standards `0x0002` sont ajoutés pour le nouveau `0x2423`, `0x2425` et `0x1052`; leurs offsets synthétiques ne servent qu'à la relocalisation de `save()`.

La piste cible doit être unique. Elle peut être vide, ou contenir seulement des macros de groupes vides déjà vérifiées; les placements audio, fades et autres événements sont refusés. L'API déplace les macros par ordre chronologique après insertion. Elle retourne `group_id`, `group_name`, `track`, `start_samples`, `length_samples` et `end_samples`.

La validation manuelle Pro Tools du 12 août 2026 a confirmé un groupe vide de 1 seconde, puis une session de pool normalisée dont les dix pistes ont été renommées `1` à `10` et qui contient 55 groupes vides : la piste `N` contient `N` groupes, chacun nommé avec son TC In et TC Out. Cette validation couvre l'enchaînement `rename_track()` puis `create_empty_clip_group()` à répétition sur plusieurs pistes.

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

L'opération complète est transactionnelle. Sans racine `0x262c`, `delete_clip_group()` retourne `0`; une dissolution réussie retourne `1`. La création n'est exposée que dans le profil 11.1; les autres liens autour de `0x2428` restent non documentés et ne doivent pas être généralisés par inférence.

## 12. Limites fonctionnelles consolidées

- Édition de sessions PTX existantes seulement; aucune création complète depuis zéro. Le seul authoring de session est le builder audio générique fondé sur un template natif strict.
- Payloads métier little-endian seulement.
- Conversions temporelles limitées à 24, 23.976 non-drop, 29.97 Drop Frame et, dans le changement non publié, 25 non-drop. Le mapping natif 25 fps et la sortie de déplacement ont été validés dans Pro Tools.
- Audio et fondus seulement sur la timeline. MIDI, contrôleurs continus, pistes/clips vidéo, Inserts, Sends, authoring I/O général, Pan, Mute automation et automation de plugins ne sont pas pris en charge. La lecture `get_track_outputs()` (§4.1.3/§4.1.6) couvre les bus mono/stéréo autonomes sur pistes Audio mono. `set_track_output()` exige des playlists nommées, réutilise un code actuel ou calibre depuis au moins deux bus distincts cohérents. Mono→Mono, Mono→Stereo avec deux pans vides vérifiés et Stereo→Mono avec pan statique centré exact et sans référence standard à la lane sont validés manuellement (§4.1.7). Stereo→Stereo préserve le pan centré statique exact et est validé manuellement (§4.1.8); réaffectation ou retrait d'un pan non centré/automatisé restent refusés. Aucune création de chemin, piste Audio stéréo, pan général ou autre routing multicanal n'est ajouté; un seul bus distinct affecté ne permet pas la calibration.
- Aucune création/suppression/réorganisation de pistes, import/export audio général ou suppression arbitraire de définitions/événements. Les exceptions étroites sont le renommage et la visibilité de slots de piste précréés (4.1.1), le clonage WAV exact décrit en 6.4 et le peuplement audio par manifeste d'un template compatible décrit en 6.5.
- Le builder audio 6.5 reste limité aux sessions 48 kHz/23,976 et aux WAV mono WAVE_EXTENSIBLE IEEE float 32 bits munis des métadonnées BWF/UMID requises. Il n'accepte que les profils natifs `native_float_15_142` et `native_float_31_151_u32` documentés ci-dessus; il ne choisit ni ordre, ni regroupement, ni piste à partir d'une convention de filename. Ces politiques appartiennent aux applications clientes.
- Création de Clip Group limitée aux deux profils stricts documentés : le profil audio template-driven 11.1 (une région audio source existante, un prototype interne vide, un composant, une piste et une macro prototype unique) et le profil vide 11.2 (48 kHz/23.976, playlist cible vide ou ne contenant que des macros vides vérifiées, IDs `0..255`). La dissolution reste limitée au cas simple documenté.
- `create_subclip()` et les trims produisent les combinaisons vérifiées offset UInt24/longueur UInt32 (`01 30 40`) et offset UInt32/longueur UInt24 (`01 40 30`). Ils rejettent encore un sous-clip virtuel d'offset nul et de longueur supérieure à UInt24, ainsi que la combinaison offset UInt32/longueur UInt32, faute de référence Pro Tools. Le split accepte les racines courtes et longues vérifiées et une longueur droite UInt32, mais la coupe relative reste limitée à UInt24 tant que le layout du fragment gauche d'un split tardif n'a pas été observé.
- Move, duplicate, split et trims refusent les placements avec fondus attachés; la duplication ne clone pas les fades.
- Fades ajoutés seulement; aucune édition/suppression de fade existant. Crossfade centré Equal Power seulement.
- Marqueurs ponctuels ajoutés seulement; aucune édition/suppression, sélection ou propriété avancée.
- Édition de Clip Gain statique seulement; l'extension mixte 9.2 préserve uniquement l'enveloppe quatre nœuds vérifiée. Cible statique existante non partagée et clonage partagé 9.3 validés dans Pro Tools; ajout d'un premier gain mixte 9.4 implémenté mais sortie manuelle encore à valider. Pas d'édition d'enveloppe ou d'index UUID moderne. Volume publié : ajout/remplacement de nœuds sans suppression. Nouveau remplacement/fusion 10.2/10.4 non publié : profils mono et stéréo/mixte validés dans Pro Tools. Remplacement exigeant une enveloppe non vide; aucun writer multi-cible/automation générale.
- Résolution exacte du fichier physique lorsque le catalogue indexé `0x1004`/`0x103a` vérifié est présent; repli nominal sans garantie dans les autres layouts. Le lecteur public ne vérifie pas l'UUID BWF, tandis que le relink renouvelle et synchronise explicitement l'identité BWF/PTX de son clone.
- Arbres limités à 128 niveaux, `block_type` pris en charge sur 8 bits, contenus/offsets sur UInt16/UInt32 et fichiers sérialisés dans l'espace UInt32.
- Les révisions PTX non présentes dans le corpus peuvent contenir des flags, géométries ou conteneurs inconnus; ils sont préservés lorsqu'ils restent opaques, mais une opération qui doit les interpréter les rejette.

## 13. Catalogue exhaustif des erreurs

La portée d'« exhaustif » est la suivante : toutes les familles d'échecs explicitement détectées ou propagées par `pt_api.py` 1.5.2, ainsi que tous les messages Pro Tools consignés dans le corpus et l'historique des essais du projet. Elle ne prétend pas recenser les messages possibles de toutes les versions de Pro Tools.

Le source courant contient 844 instructions `raise` : 724 `ValueError`, 69 `TypeError`, 13 `NotImplementedError`, 13 `OverflowError`, 8 `FileNotFoundError`, 3 `FileExistsError`, 1 `OSError` et 13 relances nues de l'exception originale.

### 13.1 Messages observés dans Pro Tools

| Message affiché | Causes techniques couvertes | Prévention/réparation |
|---|---|---|
| **Magic ID does not match** | `0x0001` pointe au mauvais offset; un enregistrement standard ou un offset secondaire de `0x0002` est obsolète; un bloc a été supprimé sans purger son pointeur; une relocalisation a utilisé un offset dupliqué; un miroir brut de suppression de piste a été conservé ou mal compacté. | Recalculer tous les offsets, patcher `0x0001`, relocaliser `0x0002`, purger exactement les enregistrements et groupes de métadonnées natifs supprimés, puis refuser les `original_offset` dupliqués. |
| **Unexpected stream type** | Un faux bloc a été créé par un `0x5A` fortuit; `block_type`, taille ou `content_type` ne correspondent plus au flux attendu; l'ordre ou l'enveloppe d'un bloc a été altéré. | Garder les payloads fixes à plat, conserver les octets opaques, sérialiser l'en-tête générique dans l'ordre exact et ne générer que les dispositions vérifiées. |
| **End of stream** | Bloc/payload tronqué; taille déclarée trop grande; compteur de `0x0002`, `0x1054`, `0x1052`, `0x2030`, `0x2424`, `0x2426`, `0x262a`, `0x262c`, `0x2630`, `0x2637` ou `0x260a` supérieur aux données réelles; compteur de série `0x0002` non ajusté; table `0x0002` amputée par un faux enfant; ajout de padding d'alignement; longueur de nom décalant la queue. | Valider toutes les tailles et compteurs avant mutation/sauvegarde, ne jamais aligner artificiellement les chaînes/blocs, garder `0x0002` plat et retirer uniquement les records standards ou groupes de métadonnées explicitement vérifiés. |
| **Cannot open the selected file because end of stream encountered** | Variante d'interface du même échec **End of stream**, observée lors des premiers essais de dissolution de Clip Group et de crossfade dont la structure sérialisée était incomplète. | Appliquer les mêmes contrôles que pour **End of stream**, en particulier compteurs, padding, payloads fixes et intégrité complète de `0x0002`. |

### 13.2 Exceptions de l'API

| Exception | Conditions exhaustives par famille |
|---|---|
| `TypeError` | Chemin non path-like ou résolu en `bytes`; tampon non `bytes`/`bytearray`; enum/composants/timecode/timestamp/index non entiers; paramètres `mute`/endianness/`include_fades` non booléens; champs `PTBlock` ou `base_offset` du mauvais type; item d'arbre non `bytes`/`bytearray`/`PTBlock`; nom attendu non `str`; gain/volume non convertibles en réel; offset/longueur de sous-clip ou montant de trim non entier; type/forme de fade non `str`; `clip_specs` non itérable ou fourni comme une chaîne, des octets, un chemin ou un mapping unique; descripteur individuel non mapping; `track_name`, `physical_filename`, `clip_name` ou `group_name` non `str`; `placement_start_samples`, `start_samples` ou `length_samples` non entier ou booléen; `session_name` non `str`/`None`. |
| `ValueError` — enveloppe et temps | Chemin vide; fichier/en-tête trop court; signature/version/endianness/mode XOR invalide; delta XOR introuvable; sample rate non fini, non positif ou hors UInt32; cadence inconnue; composant/timecode/drop-frame invalide; sample négatif; conversion temporelle non représentable. |
| `ValueError` — arbre, parsing et sauvegarde | `block_type`, `content_type`, tailles ou offsets hors bornes; profondeur >128; cycle; `original_offset` dupliqué; `0x0001` absent/invalide/mal placé; `0x0002` absent/dupliqué/non final/non EOF/non plat/vide; liaison `0x0001→0x0002` fausse; record/suffixe/compteur de série `0x0002` invalide; cible de pointeur inconnue; relocalisations chevauchantes; métadonnées `0x1028`/`0x204d` absentes, dupliquées ou tronquées. |
| `ValueError` — pistes et événements | Racines `0x1054` ambiguës; compteurs `0x1054`/`0x1052` incohérents; header, nom UTF-8, compteur ou structure `0x1050→0x104f` invalide; queue audio inconnue; ID de clip timeline inconnu; piste/placement absent ou ambigu; timestamp cible hors champ de stockage; pool de pistes dans le profil UI pré-normalisation observé, qui doit être ouvert et sauvegardé une fois dans Pro Tools avant son premier renommage. |
| `ValueError` — playlists anonymes mono | `Track name cannot be empty.` dans les validateurs de mutation; `Mixed named and anonymous main playlists are not supported.`; `Unsupported anonymous main playlist header.`; ou `Track name cannot be empty: unverified anonymous mono track catalog (...)`. Les raisons couvrent les racines `0x1015`/`0x2107`/`0x2519`/`0x2624` absentes ou ambiguës, champs non bruts, longueur de nom tronquée/invalide, UTF-8/NUL, compteurs de descripteurs/métadonnées/miroirs/slots, structure et profil mono/ordinaux de descripteur, noms dupliqués, nom ou identité de métadonnées contradictoires, identités dupliquées/nulles, familles de miroirs absentes/mal ordonnées, miroir vide, type mono/nom/identité/ordre de miroir inconnus, chemin/identité/ordre de slot inconnus. Tous sont des refus explicites sans mutation, jamais une omission de piste ni un fallback count-only. |
| `ValueError` — lecture des sorties bus | `Unsupported mono bus track-output profile (...)` (libellé historique conservé) : identité Audio mono non vérifiée; noms playlist/catalogue contradictoires; payload brut absent; longueur de nom tronquée/invalide, UTF-8 ou NUL; catalogue live `0x2603` absent/ambigu, compteur incohérent ou chemins imbriqués; conteneur de sortie principal absent/ambigu; nombre/position de `0x260e` hors profil à un descripteur; header, réservés ou trailer de sortie non vérifiés; bus sélectionné absent ou homonyme; géométrie mono autonome invalide; identité ou largeur en désaccord avec le descripteur; alias d'identité parmi les chemins mono/stéréo reconnus. Pour la nouvelle géométrie : `Unsupported bus track-output profile (selected path is not a verified autonomous stereo bus).` en cas de préfixe, taille 44, largeur 2, champ autonome/bus, magic, identité non nulle ou suffixe zéro invalide. Les erreurs de map `0x1054`/`0x1052` restent propagées. Aucune liste partielle, aucune mutation. |
| `TypeError` / `ValueError` — réaffectation mono bus | `track_name`/`output_name` non `str` (`TypeError`), vide/NUL/non encodable en UTF-8 (`ValueError`); tous les refus de map principale nommée et du validateur commun des sorties; `Unsupported mono bus output descriptor block type.`; `Conflicting descriptors for an already-used mono bus.`; `Ambiguous code shared by different mono bus outputs.`; `Track not found in verified mono Audio outputs: ...`. Pour une cible non affectée : `Unsupported unused mono bus output catalog block types.`; `Target mono bus is missing or ambiguous in the live catalog: ...`; géométrie mono autonome invalide via le helper partagé; `Ambiguous identity for the unused mono bus target.`; `Unused mono bus code calibration requires at least two distinct assigned buses.`; `Inconsistent or negative mono bus code calibration.`; `Calibrated mono bus output code is outside UInt8 range.`. Tous les refus précèdent le remplacement unique, sans mutation ni fichier créé; appeler `save()` séparément. |
| `ValueError` — transition de bus stéréo | Tous les refus communs de réaffectation/calibration précédents s'appliquent aussi aux noms de bus stéréo. `Bus routing requires two verified direct pan containers.` pour compte/type/emplacement des pans inconnus; `Mono-to-stereo requires the verified empty pan containers.`; `Stereo-to-mono requires the verified centered static pan lane.` et `Stereo-to-stereo requires the verified centered static pan lane.` (message construit selon la largeur cible); pour réduction vers mono seulement : `Stereo-to-mono requires an unambiguous pan pointer table.`; `Stereo-to-mono requires a nonempty standard pan pointer table.`; `Stereo-to-mono pan lane has unverified pointer references.`. Les refus propres au payload/layout `0x0002` sont également propagés dans cette réduction. Alias d'identité contrôlés entre les deux géométries. Tous les refus précèdent les mutations du route/pan/métadonnées de suppression, aucun fichier créé. |
| `TypeError` / `ValueError` / `NotImplementedError` — suppression de pistes | `track_names` est une chaîne, n'est pas itérable, est vide, contient un nom non `str`, vide, NUL ou dupliqué; nom absent; tentative de supprimer toutes les pistes; miroir unique `0x1054`/`0x1015`/`0x2107`/`0x2519`/`0x2587`/`0x2624`/`0x202b` absent, ambigu, tronqué, mal ordonné ou incohérent; liste/compteur/ordinal/pointeur brut `0x0002` non natif; ou playlist contenant un événement. Le dernier cas retourne `NotImplementedError`; tous les autres rejets se produisent avant écriture. |
| `ValueError` — clips et noms | `0x262a`/`0x262c` ambigu, compteur ou définition invalide; `0x2628` tronqué, nom UTF-8 invalide, flag audio/groupe inconnu ou sélecteur de largeur autre que `0x10`/`0x20`/`0x30`/`0x40`; clip absent/ambigu; nouveau nom vide, NUL, non UTF-8, trop long ou déjà présent; ID source inconnu; modèle `0x2629` sans unique identité 48 octets; offset/longueur de sous-clip direct négatif, nul ou hors UInt32; layout offset nul/longueur UInt32 ou offset UInt32/longueur UInt32 non vérifié. |
| `ValueError` — média physique et relink | RIFF/WAVE invalide, big-endian, tronqué, de taille ou d'alignement incohérent; chunk `bext`/`minf`/`regn`/`umid` absent, dupliqué ou trop court; `fmt ` ou `data` du clone/rendu absent, dupliqué ou tronqué; rendu autre que PCM/WAVE_EXTENSIBLE PCM, format PCM incompatible ou taille `data` différente; UMID, stem complet ou abrégé, paire de tokens ou références temporelles `bext`/`regn` invalides ou non concordantes avec le PTX; basename source différent du catalogue PTX ou du stem `regn`; chemin source et destination identiques; extension autre que `.wav`; stems non UTF-8, identiques ou de longueurs UTF-8 différentes; catalogue `0x1004`/`0x103a`, compteurs, noms, ordinaux ou index média invalides/ambigus; suffixe d'enregistrement WAV inconnu ou mélange des variantes `EVAW`/nulle; queue `0x103a` tronquée, sans nœud parent, avec libellé vide/NUL/non UTF-8, marqueur inconnu, terminaison invalide ou compteurs non conformes à `N+1..N+K`, `N+K+2`, `N+K+1`; nouveau nom physique déjà catalogué ou trop long; enregistrements fixes `0x2629` de 48/104 octets impossibles à réassembler ou mal ordonnés; `0x1001`, `0x2628`, ou header source `0x2106` absent, ambigu, tronqué ou de layout inconnu; header `0x2106` source <142 octets; layout source hors du parent/racine spécial ou des flags natifs de production `0x0000`/`0x0001`/`0x2000`/`0x2001`/`0x3000`/`0x3001`/`0x4001`; référence BWF différente de celle du header source; timestamp relink hors UInt32; nouvelle définition de clip en collision; placement exact absent ou ambigu. |
| `ValueError` — relink Premiere | Header source `0x2106` absent, ambigu ou <142 octets; référence BWF du WAV différente de celle lue dynamiquement dans le header source; placement antérieur à `src_offset`; variante virtuelle Premiere autre que les marqueurs observés `0x04`/`0x84` (sélecteur `0x30`, constante `0x08`). |
| Pro Tools — validation externe bloquante | `Could not complete your request because end of stream encountered` : obtenu sur des sorties de relink de clips virtuels Premiere, y compris avec un catalogue hybride créé par import Pro Tools. Ce message démontre que la structure écrite n'est pas encore supportée; il ne faut pas publier ni déployer ce chemin. |
| `ValueError` — builder audio | Manifeste vide; descripteur sans `audio_path` ou `track_name`; nom de piste, filename physique ou nom de clip vide/NUL/non UTF-8; `physical_filename` contenant un chemin ou n'ayant pas l'extension `.wav`; filename physique ou nom de clip dupliqué sans tenir compte de la casse; placement négatif, hors UInt64 ou dont la fin dépasse UInt64; RIFF invalide; `fmt `/`fact`/`data`/`bext` absent, dupliqué, tronqué ou trop court; format autre que WAVE_EXTENSIBLE float mono 48 kHz/32 bits; byte rate, block align, valid bits ou GUID incompatibles; `data` vide/non aligné; `fact` différent de la durée ou durée hors de la largeur du profil; référence BWF hors UInt32; basic UMID nul; template autre que 48 kHz/enum `0x09`, sans piste visible, noms de pistes dupliqués, piste demandée absente, contenant un événement de timeline visible ou caché, prototype/catalogue non unique, lien média non zéro, ou mélange ne correspondant à aucun des profils stricts `15/142/UInt24` et `31/151/UInt32`; catalogue/playlist/clone incohérent; racine ou nombre de définitions générées incohérent; record fixe autre que 48/104 octets; ID de définition ou index média différent de son ordinal; nom de session vide/NUL ou contenant un séparateur; rechargement généré ne concordant pas avec le manifeste. |
| `ValueError` — opérations de montage | Aucun placement visible; plusieurs placements; cut hors du clip ou partagé par plusieurs occurrences; source de split hors layout racine vérifié; coupe/offset source relatif hors UInt24; timestamp de coupe ou nouveau timestamp de Start Trim hors UInt32; payload audio non 35 octets; montant de trim nul/négatif/trop grand; composant d'image invalide ou label Drop Frame interdit. Toute opération transactionnelle restaure l'état avant de relancer l'erreur. |
| `ValueError` — fondus | `0x2630` absent/dupliqué; compteur ou payload `0x262f` invalide; nombre d'événements et géométries différent; ID inconnu/dupliqué; taille géométrique autre que 22/26/27/29/34; profil UInt24 29 octets invalide (`Unsupported 0x262f UInt24 Fade Out layout.`); durées répétées divergentes (`Inconsistent 0x262f Fade Out durations.`); durée UInt24 nulle (`Fade Out duration must be positive.`); association audio absente/ambiguë; début calculé négatif; durée nulle pour crossfade ou >UInt16 lors de la création; type/forme invalide; clip de durée nulle; cible hors clip/ambiguë; fade dépassant les bornes ou déjà existant. |
| `TypeError` / `ValueError` — marqueurs | Session sans playlist principale; règle `0x2030` absente/dupliquée/mal formée; compteur incohérent; payload `0x2077`, longueur ou UTF-8 invalide; index dupliqué/hors `1..65535`; timestamp hors Int64; nom NUL/non UTF-8/trop long; modèle ou zone UUID interne invalide. Pour le filtre `marker_track_name` : argument non `str`, vide ou NUL; catalogue `0x2519 → 0x251b → 0x251c` absent, ambigu, tronqué, dupliqué ou non UTF-8; nom de règle absent; segment final d’assignation `0x2077` absent, de longueur autre que 8, réservé non nul ou ordinal inconnu. |
| `ValueError` — Clip Gain | Dictionnaire `0x2637` absent/dupliqué/mal formé; compteur trop grand/incohérent ou octets restants; header de record absent/magic invalide, taille déclarée dépassant le payload; header statique non vérifié ou ancienne valeur non finie; header d'enveloppe quatre nœuds (padding/count/flags/segments/réservé) non vérifié, première position non zéro, timestamps non croissants ou valeurs non finies; index de définition hors dictionnaire; gain entrant `NaN`/`+inf` ou hors Float32; payload de clip trop court. Tous les refus précèdent l'écriture. |
| `ValueError` — Volume | Nom de piste vide/NUL/ambigu; association visible→`0x261c` impossible; `0x2619`, `0x260d` ou `0x260a` absent/ambigu/mal formé; magic, taille, padding, terminateur, compteur de nœuds ou segments incohérent; timestamps non strictement croissants; timestamp hors UInt32; valeur non finie ou hors Int16 déci-dB. |
| `TypeError` / `ValueError` / `OverflowError` — remplacement/fusion Volume Audio | Tous les refus de profil/enveloppe du lecteur 10.1/10.3, y compris une autre piste malformée ou largeur/index/identité de stéréo non corroborés. Nom non `str`, vide/NUL/non UTF-8/absent; `replace` non booléen; ordinal non entier/non booléen, négatif ou différent du slot live de piste (pas de playlist); nodes non itérable; paire chaîne/octets, non itérable ou longueur autre que deux; sample booléen/non entier/hors UInt32; doublons entrants; dB booléen/non convertible/non fini, produit non fini ou arrondi hors Int16; remplacement vide (`TypeError`/`ValueError` selon condition). Compteur dépassant `(0xffffffff - 26)//6` : `OverflowError`. Exceptions natives de l'itérateur utilisateur propagées. Aucune mutation avant validation et préparation intégrales; fusion vide/identique autorisée. |
| `TypeError` / `ValueError` — lecture Volume Audio mono/stéréo | `track_name` autre que `str`/`None` (`TypeError`), vide/NUL/non UTF-8 ou absent (`ValueError`); tous les refus de map nommée et des quatre miroirs d'identité. `Unsupported Audio volume profile (...)` pour racine descriptor absente/ambiguë, identités/largeurs/index de playlists/ordinaux de slots non corroborés, compte ou ordre des noms de playlists de canaux divergent; nom absent : `Track not found in verified Audio volume lanes`. Les refus internes de catalogues mono sont propagés ou enveloppés; le validateur mono par défaut n'est pas élargi. `Unsupported mono volume profile (...)` (message privé partagé conservé) pour slots imbriqués, ownership/type du state, géométrie 13 items, ordre/type des lanes ou sélecteurs bruts inconnus, lane Volume autre qu'un unique payload brut; `Invalid verified mono volume envelope (...)` pour magic/taille minimale/padding/terminateur, taille ou compteur nul/incohérent, flags/réservé/segments non vérifiés, timestamps non uniques/croissants. Validation complète avant filtre, y compris enveloppe malformée d'une autre piste; aucun résultat partiel ni mutation. |
| `ValueError` — Clip Groups | Nom vide, NUL ou UTF-8 invalide; groupe absent ou nom déjà défini; `start_samples`/`length_samples` non entier, négatif, nul (durée) ou hors UInt32, ou fin hors UInt32; racines, compteurs, noms UTF-8 ou métadonnées `0x262c`/`0x2428`/`0x2424`/`0x2426` incohérents; template vide hors 48 kHz/enum `0x09`; groupe existant ou piste cible hors profil vide vérifié; prototype non placé/placé plusieurs fois; source audio cible absente ou ambiguë; payload `0x2628`/`0x2523`/`0x2423`/`0x2425` ou queue/flag de macro non conformes; playlist cachée vide/mal formée pour la dissolution ou non vide pour la création audio-backed; enregistrement `0x0002` prototype absent/ambigu; macro visible `00 00 01` dont l'ID ordinal n'existe pas dans `0x262c`. |
| `NotImplementedError` | Exactement treize sites de refus explicites : piste non vide pour `delete_tracks()`; création de groupe vide au-delà de l'ID 255 dans le trailer vérifié; création audio-backed au-delà de la limite de son trailer vérifié; session contenant plus d'un Clip Group pour la dissolution; groupe de dissolution contenant plus d'une piste; groupe de dissolution imbriqué/avec fade/événement non audio; groupe de dissolution placé zéro ou plusieurs fois au lieu d'une; move avec fade attaché; duplicate avec fade attaché; split avec fade attaché; trim avec fade attaché; taille de record Clip Gain hors 0x16/0x2e; cible d'enveloppe Clip Gain. Les deux derniers refus ne modifient aucun record/index; le partage statique est cloné selon §9.3, l'index -1 reçoit un nouveau point selon §9.4. |
| `OverflowError` | Offset de bloc sérialisé hors UInt32; payload `PTBlock` hors champ de taille UInt32; bloc dépassant l'espace fichier UInt32; enveloppe Volume mono reconstruite dépassant la taille de bloc UInt32; timestamp restauré d'un groupe ou fin calculée d'un placement de Clip Group hors UInt64; nouvel index de point Clip Gain hors Int32 signé; nouvel ID de clip, index média de relink ou compteurs de noms physiques hors UInt32. |
| `FileNotFoundError` | Fichier d'entrée absent (propagé nativement); dossier de destination inexistant pour `xor_session()`/`save()`; WAV source ou WAV de remplacement absent, ou dossier du nouveau WAV inexistant pour `relink_clip()`; template/WAV source du builder absent ou parent du dossier de livraison inexistant. |
| `FileExistsError` | Le chemin du nouveau WAV demandé à `relink_clip()` existe déjà; le dossier cible du builder existe avant l'appel ou apparaît pendant la génération. Aucun écrasement n'est permis. |
| `PermissionError` et autres `OSError` natifs | Erreur d'ouverture/lecture/création/remplacement du système de fichiers; elles conservent leur sous-type. La recherche facultative dans `Audio Files` est la seule exception : ses `OSError` sont absorbées et la résolution continue avec `0x1004`. |
| `OSError` explicite | Écriture chiffrée plus courte que la taille attendue. Le fichier temporaire est supprimé et la destination existante reste intacte. |
