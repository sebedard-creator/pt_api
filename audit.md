# Audit technique — `pt_api.py` (v0.6.1)

**Portée :** lecture complète de `pt_api.py` (1771 lignes), croisée avec `architecture.md`, `pt_format_specs.md` et `README.md`.
**Méthode :** analyse statique ligne par ligne. Aucun fichier `.ptx` réel n'a été fourni pour tester à l'exécution — les points marqués **[À VÉRIFIER SUR FICHIER RÉEL]** sont déduits du code/de la doc mais méritent un test empirique avant conclusion définitive. Les autres bugs (section 1) sont certains, déductibles du code seul (erreurs de syntaxe/portée, code mort prouvé par grep).

---

## Résumé exécutif

| # | Sévérité | Constat | Impact |
|---|---|---|---|
| 1.1 | 🔴 Critique | `add_crossfade()` plante toujours (`NameError`) | Fonction **inutilisable telle quelle** |
| 1.2 | 🔴 Critique | `duplicate_clip()` contient un bloc mort copié de `rename_clip()`, avalé par un `except:` nu | Retourne toujours `0`, masque des erreurs |
| 2.1 | 🟠 Majeur | `0x2077` sert à la fois de "marqueur" et de "cache de fondu piste" — le template de fondu contient littéralement le texte `MARKER_ALPHA` | `get_markers()` risque de renvoyer de faux marqueurs sur toute session avec fondus |
| 2.2 | 🟠 Majeur | Deux logiques différentes pour résoudre l'ID d'un clip (position ordinale vs champ embarqué) selon la méthode | `mute_clip()`/`move_clip()` pourraient cibler le mauvais clip sur des sessions éditées nativement dans Pro Tools |
| 2.3 | 🟠 Majeur | `split_clip()` rejette des clips que `get_clips()` reconnaît comme normaux | Faux refus "clip déjà scindé" |
| 2.4 | 🟠 Majeur | Chevauchement d'octets dans la géométrie de fondu de `add_crossfade()` (offset 11 écrit deux fois) | Pré-roll potentiellement corrompu |
| 2.5 | 🟠 Majeur | Frame rate non reconnu → repli silencieux sur 24fps | Timecodes faux sans avertissement (25fps, etc.) |
| 3.1 | 🟠 Majeur | `encode('ascii')` strict vs `decode('ascii','ignore')` incohérent | Noms de clips/pistes/marqueurs accentués (français québécois) cassent les recherches par nom |
| 4.x | 🟡 Mineur | Code mort, duplication, imports redondants, commentaires de debug non nettoyés | Dette technique, contredit les conventions du projet lui-même |
| 5.x | 🟡 Mineur | `except:` nus, absence de validation dans le moteur XOR, double sérialisation dans `save()` | Robustesse et performance |

---

## 1. Bugs critiques (plantage garanti ou code mort avalé)

### 1.1 `add_crossfade()` — `NameError` garanti (`insert_after` n'existe pas dans cette portée)

```python
# ligne 1195
b1052.items.insert(ev_idx + (1 if insert_after else 0), new_1050)
```

`insert_after` est défini localement dans `add_fade()` (lignes 1306 et 1317), une **autre méthode**. Dans `add_crossfade()` (lignes 1129–1206), cette variable n'est jamais assignée. **Tout appel à `add_crossfade()` plante avec `NameError: name 'insert_after' is not defined`.**

Autrement dit, malgré tout le travail de reverse-engineering documenté dans `architecture.md` sur la structure du crossfade (3 événements `0x1050`, cache `0x2077`, etc.), **la méthode qui assemble tout ça n'a jamais pu s'exécuter jusqu'au bout**. Le README liste `add_crossfade()` comme fonctionnalité supportée — ce n'est actuellement pas le cas.

À corriger : décider explicitement où insérer l'événement de fondu (juste avant `ev_02`, cf. logique décrite en 3 points dans `architecture.md`) sans dépendre d'une variable d'une autre fonction.

**Bug additionnel dans la même méthode :** avant même d'atteindre la ligne 1195, la boucle de recherche de la piste (lignes 1160–1170) ne vérifie jamais que `b1052` a été trouvé :

```python
# pas de "if not b1052: raise ValueError(...)" avant la ligne 1173
for i, ev in enumerate(b1052.items):   # ligne 1173 — plante avec AttributeError si track_name est introuvable
```

Contrairement à `mute_clip()`, `move_clip()` ou `add_fade()` qui lèvent une `ValueError` propre quand la piste n'existe pas, `add_crossfade()` plantera avec une `AttributeError: 'NoneType' object has no attribute 'items'` peu explicite.

### 1.2 `duplicate_clip()` — bloc entier de code mort copié-collé de `rename_clip()`

`duplicate_clip(self, clip_name, hh, mm, ss, ff, mute=False)` (ligne 732) fait bien son travail de duplication jusqu'à la ligne 822 (`print(...)`). **Immédiatement après**, on trouve un second bloc (lignes 823–865) qui est une copie quasi identique de la logique de `rename_clip()` :

```python
# lignes 839-865, à l'intérieur de duplicate_clip()
renamed_count = 0
for r_def in all_regions:
    for b2628 in find_blocks(r_def, 0x2628):
        ...
        if name == old_name:              # ligne 849 — old_name n'existe pas dans duplicate_clip() !
            ...
            new_name_bytes = new_name.encode('ascii')   # new_name non plus
            ...
return renamed_count
```

`old_name` et `new_name` ne font pas partie de la signature de `duplicate_clip`. Le `NameError` qui devrait logiquement se produire à la ligne 849 est **silencieusement avalé** par le `except: pass` nu de la ligne 862, qui entoure tout le bloc. Conséquences :

- Le bloc s'exécute pour rien à chaque appel de `duplicate_clip()` (parcourt tous les clips/groupes de la session, ouvre un `try/except` par clip) — coût CPU inutile, surtout sur de grosses sessions.
- `duplicate_clip()` retourne **toujours `0`** via `return renamed_count` (ligne 865), alors que la duplication elle-même a réussi. Un appelant qui teste la valeur de retour pour savoir si l'opération a fonctionné sera induit en erreur.
- Plus largement, ce `except: pass` masque *n'importe quelle* autre erreur (pas seulement le `NameError` attendu) qui pourrait survenir dans ce bloc — mauvaise pratique qui rend le bug invisible en usage normal.

À faire : supprimer entièrement ce bloc mort (lignes 823–865) et faire retourner à `duplicate_clip()` une valeur pertinente (ex. le nouveau `clip_id`/timestamp, comme le fait `split_clip()`).

---

## 2. Incohérences architecturales majeures **[à vérifier sur fichier réel]**

### 2.1 `0x2077` : confusion entre "marqueur" et "cache de fondu de piste"

C'est la découverte la plus préoccupante de cet audit, et elle est vérifiable directement dans le code sans fichier `.ptx` :

Le template hexadécimal codé en dur dans `_ensure_fade_cache()` (ligne 1231), présenté en commentaire comme *"blank fade cache"* (cache de fondu vide à injecter sur une piste), commence ainsi :

```python
pl2077 = binascii.unhexlify("0100030900000c0000004d41524b45525f414c504841a059650a...")
```

En décodant ces octets : `4d 41 52 4b 45 52 5f 41 4c 50 48 41` = **`MARKER_ALPHA`** (ASCII), suivi de deux timestamps identiques sur 8 octets. C'est exactement la structure d'un **marqueur** telle que construite dans `add_marker()` (ligne 513 : même préfixe `01 00 03 09 00 00 0c 00 00 00`, même position de nom, mêmes deux timestamps dupliqués à la suite).

Autrement dit : **le template utilisé pour injecter un "cache de fondu" sur une piste est en réalité une copie d'un enregistrement de marqueur de test** (nommé `MARKER_ALPHA`), probablement récupéré par erreur pendant une session de reverse-engineering où les deux structures ont été confondues.

Cela a une conséquence directe sur `get_markers()` (lignes 306–323), qui parcourt **tous** les blocs racine `0x2030` et traite **chaque enfant `0x2077`** qu'il trouve comme un marqueur :

```python
marker_indices = [i for i, x in enumerate(self.root_items) if isinstance(x, PTBlock) and x.content_type == 0x2030]
for idx in marker_indices:
    container = self.root_items[idx]
    for child in container.items:
        if isinstance(child, PTBlock) and child.content_type == 0x2077:
            # traité comme un marqueur, quel que soit le contexte
```

Or `pt_format_specs.md` (section 3) documente `0x2030` comme étant le **bloc racine d'une piste** ("Contient les blocs de timeline (0x1054), cache de fondus (0x2077) et padding"), pas spécifiquement un conteneur de marqueurs. Rien dans le code ne distingue "le `0x2030` qui est la règle de marqueurs" du "`0x2030` qui est la racine d'une piste avec fondus".

**Risque concret :** sur une session réelle qui contient des fondus sur au moins une piste (donc un vrai cache `0x2077` généré nativement par Pro Tools, ou injecté par `add_fade()`/`add_crossfade()` via `_ensure_fade_cache()`), `get_markers()` risque de renvoyer des entrées fantômes — potentiellement littéralement une entrée nommée `MARKER_ALPHA` si le cache a été injecté par l'API elle-même. À l'inverse, `add_marker()` (ligne 552) insère toujours le nouveau marqueur dans `marker_indices[0]` — **le premier bloc `0x2030` trouvé dans le fichier**, sans vérifier qu'il s'agit bien de la règle de marqueurs et non de la racine d'une piste quelconque. Si l'ordre des blocs varie d'une session à l'autre, un marqueur pourrait être injecté dans le mauvais conteneur.

**Recommandation :** avant de creuser plus loin sur les fondus/marqueurs, il faut identifier un discriminant fiable (position fixe dans `root_items`, valeur de `block_type` du `0x2030` parent, présence d'un sous-bloc distinctif) pour séparer sans ambiguïté "règle de marqueurs" et "racine de piste", et ajouter une assertion dans `get_markers()`/`add_marker()` plutôt que de traiter tout `0x2077` sous tout `0x2030` comme un marqueur.

### 2.2 Deux stratégies différentes pour résoudre l'ID d'un clip

`pt_format_specs.md` est explicite : *"Le Clip ID utilisé dans la timeline (0x104f) est strictement défini par la position ordinale du bloc 0x2629 au sein de la liste 0x262a."* — c'est-à-dire que l'ID n'est **pas** un champ stocké, mais l'index de la position dans la liste.

C'est bien ce qu'implémentent `split_clip()` (compteur `current_idx`, lignes 882–891), `duplicate_clip()` (`current_idx`, lignes 747–760), `delete_clip_group()` (`clip_index`, lignes 405–418) et `get_clips()`/`set_clip_gain()`.

**Mais** `mute_clip()` (lignes 588–648) et `move_clip()` (lignes 650–705) utilisent une logique différente : elles lisent un champ entier 32 bits directement dans les premiers octets bruts du bloc `0x2629`/`0x262b` lui-même :

```python
# mute_clip(), lignes 620-624 — quasi identique dans move_clip()
p_def = next((i for i in r_def.items if isinstance(i, bytearray)), None)
if p_def:
    region_id = struct.unpack_from("<I", p_def, 0)[0]
```

`split_clip()` (lignes 1032–1045) écrit d'ailleurs explicitement l'index ordinal dans ce même champ pour les nouveaux sous-clips, ce qui suggère que ce champ *est* censé refléter la position ordinale — mais rien ne garantit que c'est encore vrai pour des clips **existants**, issus d'une session éditée de nombreuses fois nativement dans Pro Tools (suppressions, réorganisations, etc., qui pourraient faire diverger "position dans la liste" et "valeur du champ embarqué").

**Risque concret :** si ces deux valeurs divergent ne serait-ce que sur une session réelle un peu ancienne ou complexe, `mute_clip()`/`move_clip()` pourraient silencieusement muter/déplacer le mauvais événement, ou ne rien trouver et retourner `0` sans erreur. À vérifier en priorité avec un fichier réel comportant plusieurs générations d'édition Pro Tools.

### 2.3 `split_clip()` : validation de format trop stricte, incohérente avec `get_clips()`

```python
# ligne 902-905
hdr = pl_orig[offset:offset+5]
if hdr != b"\x00\x00\x30\x44\x00":
    raise ValueError("Clip is already a split or has unsupported format (needs 32-bit root format).")
```

`get_clips()` (lignes 347–359), pour ce même champ à 2 octets près (`flags = struct.unpack_from("<H", payload, 4+nlen)[0]`), accepte **deux** valeurs comme clip "normal" en 32-bit : `flags in (0x0000, 0x0001)`. `split_clip()`, lui, n'accepte qu'un seul motif précis de 5 octets débutant par `00 00`. Un clip que `get_clips()` liste sans problème comme clip complet (`flags == 0x0001`, soit octets `01 00`) sera rejeté par `split_clip()` avec un message trompeur ("clip déjà scindé"), alors que ce n'est peut-être pas le cas.

**Recommandation :** aligner la condition de `split_clip()` sur celle, plus permissive, de `get_clips()` — ou documenter clairement pourquoi les deux valeurs de `flags` ne sont pas équivalentes si c'est intentionnel.

### 2.4 `add_crossfade()` : chevauchement d'octets dans la géométrie de fondu (offset 11 écrit deux fois)

```python
# lignes 1145-1152
geom_pl = bytearray(36)
geom_pl[0:7] = b"\x00\x00\x00\x00\x00\x33\x00"
struct.pack_into("<I", geom_pl, 8, half_fade)   # écrit les octets 8,9,10,11 (pré-roll, 32-bit)
geom_pl[11] = 0                                  # "override top byte to keep it 24-bit"
struct.pack_into("<I", geom_pl, 11, fade_samples)  # écrit les octets 11,12,13,14 (durée) — écrase l'octet 11 juste remis à zéro !
geom_pl[14] = 0
geom_pl[14] = 0x01   # ligne morte : réécrite immédiatement à la ligne suivante
geom_pl[15] = 0x01
```

Le champ "pré-roll" (offset 8, censé être 24-bit d'après `architecture.md`) est écrit avec `pack_into("<I", ...)`, soit **4 octets** (8 à 11), débordant sur l'octet 11 qui appartient normalement déjà au champ "durée totale". La ligne `geom_pl[11] = 0` tente de corriger ce débordement, mais elle est immédiatement écrasée par le `pack_into` suivant qui réécrit ce même octet 11 comme premier octet de la durée. Le résultat net : l'octet 11 contient toujours le octet de poids faible de `fade_samples`, jamais réellement "0" comme l'intention du commentaire le suggère — la correction est un no-op qui donne une fausse impression de robustesse.

De même, `geom_pl[14] = 0` suivi immédiatement de `geom_pl[14] = 0x01` (deux lignes plus bas) est une ligne strictement morte.

Dans la pratique, pour des valeurs de pré-roll < 16 777 216 échantillons (largement le cas usuel), le débordement est probablement invisible (l'octet de poids fort du pré-roll serait de toute façon 0). Mais le code tel qu'écrit ne garantit rien et masque le problème plutôt que de le résoudre — à corriger en écrivant le pré-roll sur 3 octets seulement (offsets 8–10), comme le fait proprement `add_fade()` avec ses templates `fromhex` figés.

### 2.5 Frame rates non reconnus : repli silencieux sur 24fps

```python
# TimecodeEngine.get_frame_rate(), lignes 79-87
if self.frame_rate_enum == 0x01:
    return 24.0, False
elif self.frame_rate_enum == 0x09:
    return 24000 / 1001, False
elif self.frame_rate_enum == 0x05:
    return 30000 / 1001, True
else:
    # default fallback
    return 24.0, False
```

`README.md` et `pt_format_specs.md` précisent eux-mêmes que seules les sessions à 23.98, 24 et 29.97df fps ont été testées. Le problème : si une session utilise un autre frame rate (25fps PAL — courant en post-prod TV au Québec/Canada —, 30fps non-drop, etc.), le code **ne lève aucune erreur** : il calcule silencieusement tous les timecodes comme si la session était à 24fps. Toute opération basée sur un timecode (`add_marker`, `move_clip`, `split_clip`, `add_fade`...) produirait alors des positions **fausses sans avertissement**, ce qui est plus dangereux qu'un plantage franc.

**Recommandation :** remplacer le `else` par une exception explicite (`raise ValueError(f"Frame rate enum 0x{self.frame_rate_enum:02x} non supporté")`), au moins tant que ces autres frame rates n'ont pas été reverse-ingéniérés.

---

## 3. Encodage de texte — pertinent pour du contenu en français québécois

Le code mélange deux comportements incohérents pour le texte :

- **Écriture** : `name.encode('ascii')` (ex. lignes 524, 722, 852, 957, 992) — lève une `UnicodeEncodeError` si le nom contient un caractère accentué (`é`, `à`, `ç`, `œ`...).
- **Lecture** : `payload[...].decode('ascii', 'ignore')` (ex. lignes 302, 320, 346, 379, 413, 720, 887, 1060...) — **n'échoue jamais**, mais **supprime silencieusement** les octets non-ASCII. `decode('ascii')` strict (sans `'ignore'`) est aussi utilisé par endroits (lignes 618, 680, 848, 1473), entouré d'un `except: pass` qui, lui, **avale silencieusement** le clip en question (il ne sera simplement jamais trouvé).

Concrètement, pour un studio montréalais francophone :

- Créer un marqueur ou renommer un clip avec un nom du type `"Réplique-Étienne_04"` plantera `add_marker()` / `rename_clip()` avec une `UnicodeEncodeError` non gérée.
- Un clip nommé `"Étienne_04.wav"` existant dans la session pourrait être lu par `get_clips()` comme `"tienne_04.wav"` (accent muet supprimé), rendant toute recherche exacte par nom (`if name == clip_name`) impossible à faire correspondre avec le nom réel affiché dans Pro Tools.
- Les mêmes clips seraient carrément invisibles pour `mute_clip()`/`move_clip()`, qui les ignoreraient silencieusement via leurs `except: pass`.

**Recommandation :** clarifier quel encodage Pro Tools utilise réellement pour les noms (probablement Latin-1/CP1252 ou UTF-8 selon la plateforme/version), puis uniformiser tout le code sur ce même encodage, avec un comportement d'erreur explicite et cohérent (pas de mélange strict/`'ignore'`/`try-except` selon la méthode).

---

## 4. Dette technique, code mort et duplication

### 4.1 `PTBlock.serialize()` (lignes 210–230) : méthode jamais appelée, désynchronisée de `to_bytes()`

Un `grep` sur `.serialize(` ne montre qu'un seul appel, **récursif à l'intérieur d'elle-même** (ligne 214) — `serialize()` n'est appelée nulle part ailleurs dans la classe ; `save()` utilise exclusivement `to_bytes()`. Or `serialize()` ne reproduit pas le cas spécial des blocs à payload vide (`if self.original_size == 0 and len(payload) == 0`) géré par `to_bytes()` (ligne 193) — si jamais quelqu'un se remet à utiliser `serialize()` par erreur (nom intuitif, facile à confondre avec `to_bytes()`), le résultat corromprait silencieusement tous les blocs "fantômes" à taille zéro. À supprimer, ou à fusionner avec `to_bytes()`.

### 4.2 `find_blocks()` réimplémentée 5 fois au lieu de réutiliser `PTBlock.get_all_blocks()`

`PTBlock` expose déjà une méthode récursive équivalente, `get_all_blocks(content_type=None)` (lignes 232–239). Pourtant, `mute_clip()` (594), `move_clip()` (658), `duplicate_clip()` (825), `add_fade()` (1267) et `add_volume_node()` (1448) redéfinissent chacune une fonction locale `find_blocks()` strictement identique. Ça contredit directement la convention « Code minimaliste. Aucune fonction inutilisée ou dead code » de `architecture.md`, et complique la maintenance (un bug dans la logique de parcours doit être corrigé à 5 endroits au lieu d'un).

### 4.3 Imports redondants

`import struct` figure en tête de fichier (ligne 1) puis est réimporté localement dans quasiment chaque méthode (`to_bytes`, `delete_clip_group`, `add_marker`, `_parse_session_metadata`, `mute_clip`, `move_clip`, `rename_clip`, `duplicate_clip`, `add_fade`, `set_clip_gain`, `add_volume_node`, `_purge_0002_records`, `_rebuild_0002`, `save`...). Sans danger fonctionnel, mais c'est un signe clair de code assemblé par copier-coller plutôt qu'écrit de façon cohérente, et ça nuit à la lisibilité.

### 4.4 Commentaires de "notes de développement" laissés dans le code final

Plusieurs commentaires trahissent un raisonnement en cours de rédaction qui n'a jamais été nettoyé, contredisant la convention « Scripts de test éphémères supprimés après chaque phase » :

```python
# ligne 966
pl_01.append(0x00) # padding byte for the 4-byte slot in -01? Wait, -01 length is 4 bytes!
# ligne 967
# Ah, looking at my notes, -01 length is 4 bytes (a0 fe 15 00)
```
```python
# lignes 1009-1014 (split_clip)
# We don't have orig_abs_ts here yet; it's computed later in the timeline section.
# So we store a placeholder and will patch it after timeline lookup.
# Actually, we need to restructure: find orig_abs_ts FIRST.
# For now, store relative_cut_samples offset — we'll fix this at the end.
```

Fonctionnellement inoffensif ici (le correctif est bien appliqué plus loin), mais ce genre de commentaire devrait être remplacé par une explication finale et propre de *pourquoi* le placeholder est nécessaire, pas laissé comme un historique de doute.

### 4.5 `add_marker()` : pas d'auto-incrémentation de l'index

```python
def add_marker(self, name, tc_samples, index=1):
```

L'`index` par défaut est toujours `1`. Rien dans `add_marker()` n'appelle `get_markers()` pour déterminer le prochain index disponible. Deux appels successifs à `add_marker("A", tc1)` puis `add_marker("B", tc2)` sans préciser `index=2` manuellement créeront **deux marqueurs portant le numéro 1** dans Pro Tools. C'est un piège d'API facile à rencontrer en usage normal — d'autant plus que la signature ne laisse pas deviner que l'appelant doit gérer lui-même la numérotation.

**Recommandation :** faire calculer automatiquement `index = max([m['index'] for m in self.get_markers()], default=0) + 1` si `index` n'est pas fourni explicitement.

---

## 5. Robustesse et performance

### 5.1 `except:` nus (4 occurrences : lignes 625, 686, 862, 1477)

Chacun de ces blocs attrape **toute** exception (y compris `KeyboardInterrupt`/`SystemExit` en Python 2 — en Python 3 c'est limité à `Exception` et ses sous-classes typiquement visées ici, mais ça reste beaucoup trop large). Comme démontré en 1.2, un `except: pass` nu peut masquer un vrai bug (variable non définie) aussi facilement qu'un cas attendu (nom non-ASCII). Remplacer par des exceptions précises (`UnicodeDecodeError`, `struct.error`) permettrait de laisser remonter les vraies erreurs de programmation.

### 5.2 `gen_xor_delta()` : retour silencieux de `0` en cas d'échec (ligne 9)

```python
def gen_xor_delta(xor_value, mul, negative):
    for i in range(256):
        if ((i * mul) & 0xff) == xor_value:
            return (256 - i) & 0xff if negative else i
    return 0   # ← aucune erreur levée
```

Avec les deux multiplicateurs actuellement utilisés (`53` et `11`, tous deux impairs donc inversibles mod 256), une solution existe **toujours** — ce chemin est donc mort en pratique aujourd'hui. Mais si un troisième `xor_type` était ajouté un jour avec un multiplicateur pair (non inversible mod 256), la fonction retournerait silencieusement `0`, produisant une table de délta entièrement nulle (aucun déchiffrement réel) sans le moindre avertissement — le fichier de sortie serait alors un déchiffrement corrompu, indiscernable d'un vrai résultat à première vue. À corriger par une exception explicite dans le cas `return 0`.

### 5.3 `save()` sérialise chaque bloc deux fois

Dans `save()` (lignes 1693–1761), la Passe 1 (calcul des offsets, lignes 1699–1707) et la Passe 4 (sérialisation finale, lignes 1734–1743) appellent chacune `item.to_bytes(...)` sur l'intégralité de l'arbre. `to_bytes()` étant récursif, c'est le contenu complet de la session qui est reconstruit en mémoire deux fois à chaque sauvegarde. Sur une grosse session, ça double le travail CPU/mémoire pour rien : la Passe 1 pourrait mettre en cache les `bytes` déjà calculés et les réutiliser en Passe 4, plutôt que de les regénérer identiquement.

---

## 6. Recommandations priorisées

1. **Corriger `add_crossfade()`** (1.1) — actuellement inutilisable, c'est la fonctionnalité la plus visible du README qui ne marche pas du tout.
2. **Supprimer le bloc mort de `duplicate_clip()`** (1.2) et lui faire retourner une valeur utile.
3. **Clarifier la distinction marqueur / cache de fondu sur `0x2077`/`0x2030`** (2.1) avant de faire davantage confiance à `get_markers()`/`add_marker()` sur des sessions réelles comportant des fondus.
4. **Auditer `mute_clip()`/`move_clip()` contre `split_clip()`/`delete_clip_group()`** (2.2) sur un vrai fichier ayant subi plusieurs générations d'édition native Pro Tools, pour confirmer que position ordinale et champ embarqué restent toujours synchronisés.
5. **Remplacer les reployés silencieux (frame rate 2.5, `gen_xor_delta` 5.2) par des exceptions explicites** — mieux vaut un plantage clair qu'une corruption silencieuse.
6. **Uniformiser l'encodage de texte** (3.1) — probablement le point le plus concrètement gênant pour un usage quotidien en français québécois.
7. **Nettoyage de dette technique** (section 4) — mutualiser `find_blocks()`/`get_all_blocks()`, retirer `serialize()` ou le documenter comme obsolète, retirer les imports et commentaires redondants.

---

*Audit basé uniquement sur lecture statique du code fourni (`pt_api.py` v0.6.1) et de la documentation associée. Les points de la section 2 mériteraient d'être confirmés par des tests sur de vrais fichiers `.ptx`, idéalement via le harnais de test bit-perfect déjà prévu dans le `__main__` du fichier (`python pt_api.py <input.ptx> <output.ptx>`), complété par des appels aux méthodes concernées suivis d'une réouverture dans Pro Tools.*
