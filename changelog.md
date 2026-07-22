# Pro Tools API - Changelog

## v1.4.0 (Lecture des occurrences de Clip Groups) - 2026-07-22

- **Lecture des occurrences de Clip Groups** : ajout de `ProToolsSession.get_timeline_clip_groups()`. Cette API en lecture seule parcourt les macros `00 00 01` de la timeline principale, les résout dans l'espace d'identifiants `0x262c` séparé de l'audio, puis retourne chaque occurrence avec groupe, piste, plage en échantillons et timecodes. Les occurrences répétées sont conservées et le résultat est trié par position puis piste.
- **Contrat exécutable** : tests des occurrences répétées, de deux définitions homonymes distinguées par ID, du tri stable, des timecodes, de l'exclusion des événements audio et du rejet d'un ID de groupe inconnu. La suite compte maintenant 189 tests automatisés.

## v1.3.9 (Préflight sûr pour médias virtuels Premiere) - 2026-07-22

- **Nouveau préflight public en lecture seule** : `ProToolsSession.get_relink_write_status(track_name, clip_name, placement_start_samples)` résout un placement audio exact et inspecte sa définition/média sans modifier l'arbre PTX, la table de pointeurs ni un WAV. Il distingue le média virtuel natif vérifié, le média non virtuel et les layouts non vérifiables.
- **Protection Premiere dans OttoAlign2** : un virtuel à header `0x2106` variable, dont le corpus `target4` de 173 octets, retourne `supported=False` et `premiere_virtual_media`. OttoAlign2 le conserve dans la copie PTX, sans WAV créé ni suffixe `_ALIGNED`, puis l'inscrit avec piste, clip, TC In, TC Out (borne de fin), durée et raison dans `OttoAlign_Report.txt`. Les autres placements restent traités normalement.
- **Validation complète** : le corpus `target4` a produit une copie PTX byte-for-byte identique à la source et le rapport attendu; l'ouverture manuelle dans Pro Tools a réussi. La suite `pt_api` compte 186 tests, dont les statuts virtuels compatible/incompatible.
- **Limite inchangée** : `relink_clip()` n'écrit toujours pas les clips virtuels Premiere. Le préflight est une barrière de sécurité, pas une implémentation de ce relink; la consolidation Pro Tools demeure le contournement validé.

## v1.3.8.1 (Audit Premiere Pro — écriture non publiée) - 2026-07-21

- **Lecture `0x2106` à longueur variable** : les références 169/173 octets Premiere Pro sont désormais documentées et reconnues à la lecture, en plus des layouts natifs 142/151.
- **Blocage relink virtuel Premiere** : les tentatives de clonage de média, de mise à jour de la référence `placement_start_samples - src_offset` et de conservation/normalisation de la référence virtuelle ont toutes produit `End of stream encountered` dans Pro Tools. Le corpus Pro Tools d'import montre un catalogue hybride, des ordinaux `0x1003` non contigus et un nouveau média natif; son écriture n'est pas encore implémentée. Cette version ne doit pas être publiée comme supportant le relink Premiere.
- **Contournement validé** : `Consolidate Clip` a converti `target4` en média parent natif `0x2106` 151 octets. OttoAlign2 a traité cette session normalement et la sortie relinkée s'est ouverte dans Pro Tools. `Save Copy In…` seul ne normalise pas le média.
- **Événements obsolètes** : `get_timeline_clips()` ignore sans mutation les événements audio vers un ID de Clip List absent. Lors d'un relink, ces événements sont retirés transactionnellement avant la création du nouvel ID, compteurs et pointeurs inclus.
- **Régression et intégration** : les tests internes couvrent l'en-tête variable, la géométrie virtuelle et l'événement orphelin, mais ils ne remplacent pas l'ouverture par Pro Tools. Les sorties `target3`/`target4`/`target5` générées sans monkey patch ne constituent pas une validation de publication; `target4` a été refusée par Pro Tools.

## v1.3.8 (Catalogue WAV et builder générique de sessions audio) - 2026-07-17

- **Nouveau layout `0x103a` observé** : prise en charge des catalogues physiques dont chaque enregistrement WAV se termine par quatre octets nuls au lieu du marqueur `EVAW`. Le suffixe doit être uniforme dans tout le catalogue et est préservé lors de chaque insertion; un suffixe inconnu ou un catalogue mélangeant les deux variantes reste refusé.
- **Identité média fixe robuste** : les 31 octets du `0x1001` sont réassemblés avant mutation lorsqu'une séquence fortuite a été interprétée comme un bloc vide. Le clone est ensuite normalisé en un payload brut unique, sans modifier l'identité source.
- **Validation OttoAlign2** : correction fondée sur une session réelle de 71 médias dont les compteurs, la hiérarchie et les index étaient déjà cohérents. Le traitement complet a ensuite produit 226 relinks indépendants sur 236 placements; les 10 autres ont été ignorés selon les règles normales. Le catalogue final contient 297 médias, aucun WAV n'est manquant et son cycle de sauvegarde sans mutation est byte-for-byte identique. Des tests automatisés vérifient les deux nouvelles variantes.
- **Builder audio générique** : ajout de `build_audio_session(template_ptx_path, clip_specs, output_session_directory, session_name=None)`. À partir d'un template Pro Tools compatible 48 kHz/23,976 et d'un manifeste ordonné de descripteurs, la fonction crée atomiquement une livraison `.ptx`/`Audio Files`, recharge le PTX et vérifie le résultat avant publication.
- **Contrat indépendant des applications** : chaque descripteur fournit explicitement `audio_path` et `track_name`, avec `physical_filename`, `clip_name` et `placement_start_samples` facultatifs. L’API ne reconnaît aucun motif de filename, ne trie et ne regroupe pas les sources, ne limite pas le template à deux pistes et ne choisit aucune piste pour l’appelant. Les overlaps conservent l’ordre du manifeste sans trim, mixage ni changement automatique de lane.
- **Import BWF sample-accurate** : prise en charge stricte des sources mono 48 kHz 32-bit float WAVE_EXTENSIBLE. La durée vient du chunk `data` et de `fact`, le placement de `bext+338` et l'identité des 32 octets du basic UMID. Le writer reproduit les variantes natives `0x1001` de 15 octets, `0x2106` de 142/58 octets et parent `0x2628` `00 00 30 04 00`.
- **Audio préservé** : chaque WAV source est copié byte-for-byte. L'API ne devine pas le cache de forme d'onde `DGDA` et ne fabrique pas `minf`/`regn`; le corpus comparatif démontre que Pro Tools conserve d'abord intégralement la source avant d'ajouter ces chunks. Une session native avec les WAV vierges, puis la sortie corrigée à deux médias, ont toutes deux réussi l'ouverture, la lecture, la sauvegarde et la réouverture dans Pro Tools.
- **Normalisation sûre des records `0x2629`** : la première ouverture comparative a révélé qu'une identité de 48 octets scindée par un faux bloc vide décalait l'ancien span du lien média. Le writer concaténait alors 208 octets au lieu de remplacer les 104 octets natifs; Pro Tools ouvrait la session, mais dédupliquait le second média vers le premier à la sauvegarde. Le lien média, situé après l'identité, est maintenant remplacé en premier. Le rechargement vérifie explicitement les tailles 48/104, chaque ID et chaque index physique.
- **Validation Pro Tools et documentation** : ajout de 12 tests du builder, couvrant notamment les filenames arbitraires, l’ordre explicite, les overrides, les overlaps, le rejet des timelines cachées non vides, le réassemblage d'un faux bloc, trois pistes et le corpus A/B réel; la suite atteint 178 tests avec `-W error`. La sortie corrigée conserve après sauvegarde native deux médias, leurs longueurs, IDs/index, timestamps et l'overlap d'un échantillon. README, architecture, spécification normative et handoff décrivent le contrat générique, les limites UInt24/UInt32 et la transaction de dossier.

## v1.3.7 (Audit de robustesse et clips longs) - 2026-07-15

- **Layouts de clips de production correctement décodés** : prise en charge des flags parent/virtuel `0x0000`/`0x0001`, `0x2000`/`0x2001`, `0x3000`/`0x3001` et `0x4001`. Les offsets source suivent le flag (0, UInt16, UInt24 ou UInt32) et les sélecteurs indépendants `0x10`/`0x20`/`0x30`/`0x40` donnent une longueur UInt8/UInt16/UInt24/UInt32. `01 30 40` signifie donc flag `0x3001`, offset UInt24 et longueur UInt32, tandis que `01 40 30` signifie le vrai flag `0x4001`, offset UInt32 et longueur UInt24.
- **Lecture audio indépendante des fondus** : `get_timeline_clips(include_fades=False)` retourne les placements audio sans interpréter les géométries de fondu. Le mode par défaut reste strict et continue de valider intégralement les fondus.
- **Validation OttoAlign2** : la session de référence d'environ 42 minutes est maintenant lue intégralement (135 régions) et les 404 placements des deux pistes cibles trouvent tous un chevauchement; la coupure historique vers `10:07:41` provenait du passage aux offsets source UInt32 et non d'une limite générale de durée.
- **Split des clips longs** : `split_clip()` accepte les racines longues `00 00 40 44 00` et produit la moitié droite native `01 30 40 44 08` lorsque sa longueur dépasse `0xFFFFFF`. La coupe relative demeure limitée à UInt24 tant qu'un fragment gauche issu d'un split tardif n'a pas été observé.
- **Trims et sous-clips longs** : `create_subclip()` et le Start Trim écrivent maintenant les combinaisons vérifiées offset UInt24/longueur UInt32 et offset UInt32/longueur UInt24. Le Start Trim d'un clip de six minutes jusqu'à `10:05:50:00` reproduit le payload Pro Tools `01 40 30`; les timestamps internes UInt32 sont ajustés au nouveau départ absolu.
- **Validation réelle des clips longs** : lecture exacte et sauvegarde bit-perfect d'un clip continu de six minutes à 48 kHz/23,976 fps et de ses références split/trim; les payloads générés correspondent octet pour octet aux références Pro Tools, et les sorties API du split à dix secondes et du Start Trim à cinq minutes cinquante ont été ouvertes avec succès dans Pro Tools.
- **Clonage et relink physique d'un placement** : ajout de `relink_clip()`, fondé sur une paire minimale avant/après créée dans Pro Tools. Par défaut, l'opération clone le WAV complet sans modifier le PCM, renouvelle et synchronise son identité BWF/UMID, sa référence temporelle et ses tokens `regn` avec un nouveau `0x1003`/`0x2629`, ajoute le nom physique au catalogue indexé `0x103a` et ne retargete que le placement demandé. Les essais v1 à v5 ont éliminé plusieurs divergences de timestamps, UUID et BWF, puis un diagnostic A/B avec média entièrement natif a isolé le compteur oublié de la queue minimale `SHARE TO NETWORK`; le diagnostic bloquait avec `2` et s'ouvre avec la seule correction à `3`. La sortie v6 avec WAV généré s'est ensuite ouverte correctement dans Pro Tools. Le writer valide désormais la forme hiérarchique générale de la queue et incrémente ses `K` compteurs `N+1..N+K` ainsi que les deux compteurs initiaux. La structure à trois niveaux `VIDEO`/`Exports`/`test ottoalign` de la session de production est reconnue sans traiter ces libellés comme des chemins; les chemins WAV restent fournis explicitement, normalement dans le dossier frère `Audio Files`. Le lien média de 104 octets est maintenant lu et écrit comme le véritable UInt32 little-endian à `+96`; un test force l'index 256 et supprime l'ancienne limite erronée à 255 médias.
- **Relink virtuel et PCM rendu** : `relink_clip()` prend maintenant en charge les layouts virtuels de production à offset nul ou non nul, préserve leur géométrie `0x2628` et leur référence BWF, accepte les variantes `0x2106` de 142/151 octets et les deux dispositions `regn` observées. L'argument facultatif `replacement_audio_path` installe un chunk PCM rendu seulement si son format et sa taille correspondent exactement au WAV source. Les relinks virtuels zéro/non-zéro et un traitement OttoAlign2 complet de 388 placements ont été ouverts, joués et confirmés dans Pro Tools.
- **Enregistrements fixes robustes** : les identités de 48 octets et liens média de 104 octets sont resérialisés autour du `0x4403` avant validation. Des octets aléatoires ressemblant à un bloc vide ne peuvent donc plus découper artificiellement ces records et bloquer le relink de certains clips de production.

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
- **Tests** : couverture automatisée portée à 164 tests, complétée par des ouvertures manuelles dans Pro Tools — relinks parent/virtuel, PCM rendu et session OttoAlign2 complète —, les no-op SHA-256 bit-perfect, les lectures publiques des sessions réelles, la validation structurelle du catalogue à trois niveaux et de l'index média UInt32, la compatibilité syntaxique Python 3.8 et la construction réussie du wheel PEP 517.
- **Documentation** : correction des signatures publiques, des largeurs 24/32 bits et de l'identité réelle de `0x2077`; le README inventorie désormais toute la surface publique stable, distingue les utilitaires internes et explicite les limites de format, de ciblage et d'édition. `pt_format_specs.md` a été refondu en spécification normative complète du code 1.3.7 : 34 symboles publics, catalogue des blocs, algorithmes d'écriture, constantes binaires, limites et inventaire exhaustif des erreurs de l'API et des erreurs Pro Tools connues. `architecture.md` est désormais un survol global distinct de cette spécification, et `handoff.md` consigne l'état validé, les sessions de référence, les risques connus et la procédure des futures révisions; normalisation UTF-8 des fichiers Markdown.

## v1.3.6 (Hotfix Flags & 24-bit Length)

- **Correction alors appliquée** : Les longueurs `0x0001` ont été masquées à 24 bits. La session longue comparative ajoutée en v1.3.7 a démontré que le quatrième octet appartient réellement à la longueur UInt32; ce masque a donc été retiré et remplacé par le décodage fondé sur le sélecteur de largeur.
- **Bug Fix alors appliqué** : ajout initial du flag `0x2001`. Les sessions de production analysées en v1.3.7 ont ensuite établi que son offset source est UInt16 et que sa longueur, comme celle des autres familles, dépend du sélecteur qui suit le flag.

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
