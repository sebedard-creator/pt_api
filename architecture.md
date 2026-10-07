# Architecture logicielle de `pt_api` 1.5.2

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

Le travail non publié ajoute aussi la lecture de la famille virtuelle native
`0x1001`, dont l'offset source tient sur UInt8. La référence de trim du début
confirme offset, durée et placement exacts; les sauvegardes API préservent les
octets. Cette extension du décodeur partagé ne modifie ni les profils produits
par les writers ni les tables strictes du relink. Les familles non vérifiées
`0x1000` et `0x4000` restent refusées. La validation manuelle de la copie API
dans Pro Tools est confirmée; les positions et l'offset exacts sont conservés
après sauvegarde native, puis deux cycles API restent byte-identiques.

### Entrées/sorties et chiffrement

Les fonctions de chiffrement valident l'en-tête PTX, transforment une copie des données et préservent le tampon de l'appelant. Les chemins `str` et `os.PathLike` textuels sont normalisés; les erreurs système gardent leur type natif. Les écritures PTX et le clonage physique d'un WAV utilisent un fichier temporaire et `os.replace()`.

### `PTBlock` et parseur

`PTBlock` représente un bloc binaire, ses données brutes et ses enfants. Le parseur conserve dans l'ordre les blocs reconnus et les segments opaques. La profondeur, les cycles, les tailles et l'unicité des offsets sont bornés afin qu'un fichier mal formé produise une erreur contrôlée.

Le correctif local non publié protège aussi les préfixes bruts explicitement connus avant la recherche récursive d'enfants : seul le compteur de playlists de la track map possède actuellement cette règle. Il ne change ni le modèle mémoire ni le flux de sauvegarde. La non-régression native a été validée par ouverture/sauvegarde/réouverture dans Pro Tools 2024.3.1, puis par les cycles de sauvegarde/rechargement de l'API sur le fichier resauvegardé.

### `TimecodeEngine`

`TimecodeEngine` centralise les conversions entre échantillons, positions et durées. Il utilise la fréquence et la cadence réellement lues dans la session; aucune valeur temporelle par défaut n'est injectée silencieusement.

L'étape 2 non publiée ajoute le mapping natif 25 fps non-drop dans ce moteur partagé, sans autre changement de flux. Les lecteurs et mutations déjà fondés sur ce moteur en bénéficient; les writers de création à profil 48 kHz / 23.976 conservent leurs validations indépendantes. Les tests temporels, la lecture native et le déplacement écrit d'une image sont validés, y compris l'ouverture/sauvegarde/réouverture dans Pro Tools puis les cycles API sur le fichier resauvegardé.

### `ProToolsSession`

`ProToolsSession` est la façade de haut niveau. Elle possède :

- les octets déchiffrés et le chemin absolu de la session;
- l'arbre racine ordonné;
- les métadonnées temporelles;
- la liste des offsets/types de blocs supprimés qui devront être purgés à la sauvegarde.

Les méthodes de lecture exposent pistes, marqueurs, clips et événements. Les markers ponctuels peuvent aussi être filtrés par le nom d’une règle de markers native : le reader associe l’ordinal final du `0x2077` au catalogue de règles `0x2519`, sans confondre cette règle avec une playlist Audio. Les méthodes de mutation couvrent les opérations audio documentées dans le README, y compris le clonage/relink physique étroit d'un placement. Les validateurs communs résolvent les noms, compteurs, placements, géométries, catalogues média et dictionnaires avant toute écriture. La hiérarchie interne d'un catalogue PTX reste distincte de la résolution des chemins : l'application fournit les chemins WAV, normalement sous le dossier `Audio Files` associé à la session.

Pour les applications qui composent une session à partir d'un pool Audio précréé, la template peut contenir plus de slots que nécessaire. `rename_track()` renomme un slot et `set_visible_tracks()` active exactement les slots nécessaires en laissant les autres cachés. `delete_tracks()` est la voie distincte de réduction du pool : elle accepte toute combinaison de slots Audio vides du profil natif vérifié, conserve au moins un slot et met à jour les miroirs de timeline, nom, affichage, index et état, ainsi que les métadonnées brutes `0x0002` associées. Elle ne crée ni ne réordonne de pistes et refuse une piste contenant des événements ou une structure non reconnue. Une template de pool doit être sauvegardée au moins une fois par Pro Tools après sa création; le profil UI pré-normalisation observé est refusé pour le renommage plutôt que réécrit par heuristique. Les suffixes UInt16 des flags `0x251a` restent opaques et sont conservés : ils ne constituent pas un identifiant de profil.

La lecture des macros de Clip Groups suit une voie distincte : `get_timeline_clip_groups()` lit les occurrences visibles de la timeline dans leur namespace `0x262c`, sans les confondre avec les clips audio de `get_timeline_clips()`. Cette séparation préserve les identifiants indépendants et les placements répétés. Deux writers utilisent ce namespace : `create_clip_group()` convertit une région audio existante à partir d'un prototype, tandis que `create_empty_clip_group()` construit le profil natif à playlist interne vide à partir d'une session vierge compatible. Aucun des deux ne crée de piste ni de média; les politiques de préparation, de nommage et d'affectation restent chez l'application cliente.

La comparaison native d'un Basic Folder confirme la séparation entre le
catalogue UI et les playlists audio : le dossier a ses miroirs de type `0x0b`
mais aucune playlist audio principale. Le lecteur conserve ces miroirs sans
les utiliser pour reconstruire des noms : `get_tracks()` retourne les deux
playlists nommées, et les clips restent associés aux pistes dedans/dehors.
Cette non-régression ne nécessite pas de modification du code de production.
La copie API a été ouverte, lue, sauvegardée et rouverte dans Pro Tools;
deux cycles API sur le fichier resauvegardé conservent les octets et les clips.
Le fallback du contributeur pour les playlists AAF anonymes demeure non
intégré; cette paire ne constitue pas une validation de ce cas différent.

Une référence native distincte issue d'un cycle Pro Tools → AAF → Pro Tools
a ensuite confirmé un profil entièrement anonyme et mono. Le helper privé
`_validated_anonymous_mono_track_names()` vérifie les descripteurs indexés
`0x1015/0x1014`, les noms et identités ordonnés `0x2107/0x210b`, les deux
familles Audio mono `0x2519/0x251a` et les identités des slots
`0x2624/0x261c/0x261b/0x102d/0x2619`. Tous les compteurs de playlists et
d'événements sont vérifiés avant cette résolution. Les trois readers de
pistes/clips/groupes autorisent ce profil explicitement; les validateurs de
mutation conservent leur défaut strict. Aucun nom n'est injecté dans l'arbre,
aucun état de correspondance n'est mis en cache et aucun writer n'est ajouté.
Les playlists mixtes, la stéréo, les dossiers et les slots affichés masqués ou
réordonnés restent hors de ce resolver; un échec est explicite, jamais une
omission silencieuse. L'API ne lit ni ne convertit l'AAF lui-même. Les cycles
API sont byte-identiques. L'ouverture et la sauvegarde de la copie dans Pro
Tools sont confirmées; le fichier resauvegardé garde ses headers anonymes et
sa géométrie exacte, puis passe deux nouveaux cycles API byte-identiques.
La lecture audio et la réouverture manuelles sont également confirmées; ce profil de lecture est validé.

### Inspection et réaffectation limitée des sorties de pistes

La nouvelle inspection I/O est une voie strictement en lecture seule :
`get_track_outputs()` réutilise la corroboration des identités mono Audio,
puis lit un unique descripteur principal au chemin
`0x2624/0x261c/0x261b/0x260d/0x260e`. Le nom et l'identité de la sortie doivent
correspondre à un bus mono ou stéréo autonome du catalogue racine live `0x2603/0x2602`,
pas aux bibliothèques `0x4501`/`0x4702`. Les autres largeurs restent opaques;
aucune recherche de magic dans les octets source, calibration de code,
mutation ou omission partielle. Ce reader n'étend pas les writers existants;
un catalogue public de toutes les sorties n'est pas intégré.
La copie issue de la paire native a été ouverte, sauvegardée et
rouverte dans Pro Tools avec les sorties et clips attendus. Le fichier
resauvegardé conserve les descripteurs et chemins I/O; deux nouveaux cycles
API restent byte-identiques. Le profil du reader est validé indépendamment
de la réaffectation limitée décrite ci-dessous.

Le nouveau `set_track_output()` partage cette validation structurelle via
`_validated_bus_track_outputs()`, mais exige des playlists nommées et
des descripteurs de type natif `0x09`. Il prévalide toutes les affectations
et refuse les codes contradictoires avant toute mutation. Pour le profil mono,
un unique payload est remplacé.
Si le bus cible est déjà utilisé par une piste actuelle, son code existe
dans l'arbre et est réutilisé sans calibration. Seuls code,
identité de bus et longueur/nom sont changés. Les blocs, offsets source,
états de piste, catalogues et trailers restent conservés; `save()` assure
la relocalisation existante. Retour 1 si modifié, 0 si identique, pas de
sauvegarde implicite. La paire native confirme le descripteur attendu;
ouverture/sauvegarde/réouverture de la sortie écrite confirmées dans Pro Tools.
Le fichier resauvegardé conserve sorties, catalogues I/O et géométrie des
clips, puis passe deux nouveaux cycles API byte-identiques.
Une nouvelle branche pour un bus inutilisé valide d'abord son identité et
sa géométrie mono autonome dans le même catalogue vivant. La racine doit
être de type `0x02` et chaque chemin direct `0x2602` de type `0x0e`.
Au moins deux bus distincts actuellement assignés doivent tous donner la
même base `code - ordinal`; le code cible est `base + ordinal`, contrôlé
dans UInt8 sans modulo. Tous les chemins directs comptent pour l'ordinal,
y compris les largeurs non sélectionnées laissées opaques. La validation
de géométrie mono est partagée avec le reader; ni cache ni code arbitraire.
Une comparaison native distante confirme cette calibration, et les cycles
API du changement/retour sont exacts. Ouverture/sauvegarde/réouverture des
deux sorties sont confirmées dans Pro Tools. Leurs sauvegardes conservent
descripteurs, catalogues et clips, puis passent deux cycles API byte-identiques
chacune et un aller-retour calibré restituant les octets d'origine.
Ce writer ne crée aucun chemin I/O et ne traite pas les Sends ou les sorties
matérielles. L'extension suivante reste limitée aux pistes Audio mono.

La branche mono→bus stéréo partage la calibration ou la réutilisation du code,
mais vérifie une géométrie de catalogue distincte et écrit le trailer de largeur
du bus cible. La paire native montre aussi la création d'un pan centré : les deux
conteneurs directs `0x260c` doivent correspondre au profil vide natif, puis le
premier reçoit une lane statique `0x260a`. Aucun pan du donneur n'est cloné.
Tout est préparé avant le remplacement du descripteur et des items du pan;
le pipeline de sauvegarde existant relocalise les offsets. La nouvelle lane
n'a pas de pointeur standard supplémentaire dans la référence native.
Reader et writer sont couverts par comparaison native et cycles API exacts;
**validation manuelle de la sortie stéréo confirmée**. La sauvegarde native
conserve les route/pan, catalogues et placements, puis passe deux nouveaux
cycles API byte-identiques. Le changement stéréo→stéréo possède désormais la
branche stricte décrite ci-dessous. Les pistes Audio stéréo et chemins plus larges restent hors profil.

La branche de retour stéréo→mono exige le pan statique centré exact et un
second conteneur vide. La paire native retire uniquement cette lane et remet
le premier conteneur à son état brut vide; l'API fait de même sans remplacer
le bloc parent ni son offset. Pour une lane à offset existant, la table de
pointeurs actuelle doit être unique, valide, avec des records standards et sans record ciblant
la lane. Son offset et type sont préparés dans les métadonnées de suppression
du pipeline de sauvegarde existant. Toutes les validations/allocations précèdent
les mutations du route, du pan et de ces métadonnées; les autres pistes restent
intactes. Pan non centré, automation et états inconnus ne sont jamais effacés.
Comparaison native et cycles API exacts réussis; **ouverture/sauvegarde/réouverture de cette
sortie de retour confirmées**. Les fichiers resauvegardé et rouvert conservent route/pans vides,
catalogues et placements; deux cycles API et une composition mono/stéréo/mono
restent byte-identiques sur chaque référence. Cette étape est clôturée;
ce n'est pas une API générale d'édition du pan.

La branche non publiée stéréo→stéréo valide les mêmes deux conteneurs et le
pan statique centré exact que le retour stéréo→mono, mais ne les remplace pas.
Après toutes les validations de destination/code/pan, seul le payload du
descripteur principal change. Lane, offsets et métadonnées de suppression
restent intacts; le pipeline de sauvegarde relocalise les pointeurs habituels.
La paire native conserve pans/catalogues/clips et les 172 records standards
avec mêmes cibles logiques. Égalité du descripteur natif, cycles API exacts et
retour à l'ancien bus reproduisant tout le fichier sont vérifiés. **Ouverture,
sauvegarde et réouverture de la sortie API dans Pro Tools confirmées.** La
sauvegarde native conserve routes/pans/catalogues/placements/cibles de pointeurs;
deux cycles API et une composition aller-retour restent byte-identiques.
Ce profil ne permet pas de réaffecter une piste au pan non centré ou automatisé.

### Clip Gain : validation de records et édition statique

Le validateur privé `_validated_clip_gain_dictionary()` parcourt chaque record
par sa taille déclarée et vérifie l'épuisement exact du dictionnaire. Il ne
cherche jamais une signature dans les données pour deviner une frontière.
Les points statiques historiques sont vérifiés; le nouveau profil mixte peut
conserver l'enveloppe à quatre nœuds observée nativement. Tous les records et
les index de toutes les définitions sont validés avant de préparer les données
à écrire. Une cible statique non partagée est modifiée en place; les autres
records restent byte-identiques. Cette branche non publiée est validée par
ouverture/lecture/sauvegarde/réouverture dans Pro Tools; la sauvegarde native
passe deux nouveaux cycles API et des compositions de gain exactes.

Une cible d'enveloppe et un profil de record inconnu sont refusés sans mutation.
Le clonage d'un gain partagé et l'ajout d'un gain restent disponibles pour le
profil entièrement statique historique. Le nouveau clonage mixte réutilise
ce chemin : copie du record
statique à son offset borné, nouvelle valeur, insertion terminale, compteur
incrémenté et seule cible reliée au nouvel index. Le point partagé et tous
les autres liens restent intacts. Ce clonage est désormais validé dans Pro Tools;
sa sauvegarde compacte les records inutilisés, conserve les gains indépendants
et passe les cycles/compositions exacts. Le partage a été construit sur une
référence native validée, pas présenté comme une paire before/after native.

La branche suivante réutilise l'ajout statique pour une cible d'index historique
-1 dans un dictionnaire mixte. Un record est ajouté à la fin et seule la cible
est reliée au nouvel ordinal. Elle attend sa validation manuelle dédiée;
la référence sans gain est construite avec la sentinelle déjà vérifiée.
Plusieurs clips d'index -1 ne sont pas un partage de point : chacun reçoit son
nouveau record indépendamment. Les mises à jour ultérieures ne réallouent pas.
Aucun resolver heuristique UUID ni writer d'enveloppe n'est ajouté. Les 26
tests Clip Gain mixtes portent la suite à 412 tests; le pipeline de sauvegarde
et les méthodes Volume ne changent pas.

### Lecture seule de l'automation de Volume

Le lecteur non publié `get_volume_automation()` utilise uniquement l'arbre
courant. `_validated_mono_volume_lanes()` corrobore les noms/identités Audio
mono par les quatre miroirs déjà validés, exige des playlists nommées et
reconnaît la géométrie exacte du conteneur principal. Il distingue la lane de
Volume des autres `0x260a` directs et du Pan imbriqué. Le décodeur vérifie
l'enveloppe complète avant de produire des points sample/timecode/dB copiés.
Un filtre est appliqué seulement après validation complète : pas de données
partielles ni de modification. Comparaison native et cycles sans mutation
byte-identiques sont vérifiés. Le writer historique `add_volume_node()` reste
inchangé. Les champs `0x1029` modifiés par la sauvegarde native
restent opaques, sans hypothèse de rôle ni synchronisation ajoutée.

L'extension de lecture stéréo passe par `_validated_named_audio_volume_lanes()`
et le validateur partagé `_validated_audio_track_identities()` : descripteurs,
identités, deux familles de miroirs de largeur et ordinaux de slots sont
corroborés. Chaque nom de piste est développé en une ou deux playlists selon
la largeur, puis comparé à toute la map principale. Un slot conserve une seule
lane Volume; la piste mono suivant une stéréo ne prend pas son index de playlist.
Le même état principal de 13 items/enveloppe est ensuite validé. La paire
native et les cycles sans mutation sont vérifiés. Le resolver anonyme conserve
son défaut mono strict; `get_tracks()` et les lecteurs timeline ne changent
pas de contrat. Le writer utilise maintenant ce resolver commun, avec le statut
de validation distinct ci-dessous.

### Remplacement/fusion de Volume mono/stéréo — non publié

`set_volume_automation()` partage la géométrie d'état et le resolver nommé
mono/stéréo du lecteur. Une seule piste/enveloppe est ciblée par appel. Toutes les
enveloppes existantes sont validées, puis tous les paramètres/points entrants;
une enveloppe malformée n'est pas ignorée même pour un remplacement complet.
La méthode prépare les points triés, le payload et le résultat avant une seule
affectation au payload de la lane cible. L'objet lane, son offset, les autres
automations, pans, `0x1029` et métadonnées de suppression sont conservés.
La fusion conserve les anciens points sauf collision exacte de timestamp;
les doublons entrants sont refusés. Remplacement vide refusé, fusion vide ou
enveloppe identique sans mutation. Aucun save implicite ni
édition simultanée de plusieurs pistes. L'égalité de l'enveloppe native,
l'identité binaire des deux modes et les cycles API passent les régressions;
**ouverture/sauvegarde/réouverture de la sortie confirmées dans Pro Tools**.
Le fichier resauvegardé conserve les points exacts et les autres lanes,
pans, routes, clips et cibles de pointeurs. Deux cycles API et des compositions
remplacement/fusion restituent ce fichier bit pour bit. Pro Tools modifie les
deux octets opaques `0x1029` observés dans l'after natif; ils restent non
interprétés et ne sont pas écrits par ce writer. Étape mono clôturée,
écriture stéréo/mixte également **validée dans Pro Tools**. La branche nouvelle
ne fait que choisir la lane
par le resolver corroboré; aucune géométrie, champ opaque ou lane Pan n'est
reconstruit. Les deux modes donnent le même fichier et retirer le point
restitue le before entier. La sauvegarde Pro Tools conserve Volume/pans L/R,
routes/autres lanes/clips et cibles de pointeurs, puis passe deux cycles API
et des compositions remplacement/fusion byte-identiques. Les changements
opaques `0x1029` correspondent à la référence native, sans interprétation
ajoutée. Étape stéréo clôturée; aucun writer multi-cible/automation générale.

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
- Une suppression de piste ne retire des métadonnées `0x0002` non standard que lorsque leur groupe natif exact a été validé; toute autre disposition est refusée.
- Les nouvelles fonctions doivent réutiliser les validateurs et mécanismes transactionnels communs.
- La bibliothèque n'écrit pas dans `stdout`; les diagnostics facultatifs passent par le logger `pt_api`.
- Toute nouvelle disposition binaire doit être confirmée par une session Pro Tools de référence avant d'être déclarée prise en charge.
- L'écriture de Clip Groups reste fondée sur des profils natifs stricts : conserver l'origine opaque de la playlist interne, les trois nouveaux enregistrements `0x0002` et le flag de macro visible documentés dans `pt_format_specs.md`; ne pas généraliser les profils audio-backed ou vides aux groupes multi-composants ou imbriqués sans une nouvelle paire native.
- Le builder audio doit rester limité au template et au WAV float validés; élargir les largeurs, formats, fréquences ou cadences exige de nouvelles références natives.

## Validation et évolution

L'étape 3 non publiée ajoute uniquement la lecture d'un profil natif de fade-out long. Le lecteur de timeline conserve ses positions exactes; les garde-fous identifient aussi ce fondu comme attaché et refusent les mutations qui ne savent pas le préserver. Le writer de création reste inchangé. Les fixtures natives à 25 et 23.976 fps couvrent la lecture, l'ouverture/sauvegarde/réouverture confirmée par l'utilisateur dans Pro Tools et les cycles API byte-identiques avant/après cette sauvegarde.

La suite automatisée constitue le premier niveau de validation. Les mutations binaires nouvelles exigent ensuite une comparaison avec une session Pro Tools de référence et, lorsque nécessaire, une ouverture manuelle dans Pro Tools.

Une révision qui change le format, une fonctionnalité ou une limitation doit mettre à jour conjointement :

- le code et ses tests;
- `pt_format_specs.md` pour le comportement binaire;
- `README.md` pour le contrat public;
- `changelog.md` pour l'historique;
- `architecture.md` seulement si les composants ou le flux global changent;
- `handoff.md` pour l'état opérationnel de la prochaine reprise.
