# Handoff — état au 27 juillet 2026

Ce document sert à reprendre le développement de `pt_api` sans perdre les connaissances acquises pendant l’audit. Il ne remplace ni la spécification technique ni les tests.

## État de reprise

- Version courante : `1.4.0`.
- **Correctif relink production post-v1.4.0** : le writer et son préflight reconnaissent maintenant les géométries parent de production `0x0000`, `0x2000` et `0x3000` en plus des racines/virtuels déjà couverts. Le flag d'origine, la largeur d'offset source et la référence incorporée sont conservés. Les vérifications isolées ont sauvegardé/rechargé des clips réels de chaque famille; l'exécution OttoAlign2 complète concernée a ensuite été confirmée réussie par l'utilisateur. Ceci ne change pas le refus explicite du relink virtuel Premiere à header variable.
- **Second profil de builder** : `build_audio_session()` choisit désormais son profil depuis le template, avant la validation des WAV. Les profils stricts sont `native_float_15_142` (parent `00 00 30 04 00`, 15/142/58, durée UInt24) et `native_float_31_151_u32` (parent `00 00 40 44 00`, 31/151/58, durée UInt32). `validate_audio_import_template()` expose ce diagnostic publiquement, sans mutation. Le profil 31/151/UInt32 possède des paires natives avant/après et des tests automatiques de construction/rechargement; toute modification future de son writer exige aussi son ouverture manuelle dédiée dans Pro Tools.
- **Blocage d'écriture Premiere, préflight sûr** : la lecture des headers `0x2106` de 169/173 octets et des marqueurs virtuels `0x3001/0x30` (`0x04`/`0x84`) est observée. Leur relink écrit n'est pas validé : les sorties `target4` ont toutes été refusées par Pro Tools avec `End of stream encountered`, y compris un contrôle utilisant un catalogue hybride créé nativement par Pro Tools. Un `Save Copy In…` du `target4` a copié le WAV mais conservé le header 173 octets, la signature Adobe Premiere et la géométrie virtuelle; il ne normalise donc pas ce cas. `get_relink_write_status()` détecte ce layout de façon observationnelle avant toute écriture et retourne `premiere_virtual_media`/173; OttoAlign2 peut donc conserver ces placements intacts, sans WAV ni suffixe `_ALIGNED`, et les documenter dans son rapport. Cela ne constitue pas un support du relink Premiere.
- **Contournement validé** : `Consolidate Clip` dans Pro Tools transforme le clip concerné en parent natif avec header `0x2106` de 151 octets. OttoAlign2 a ensuite traité `target4_consolidated_reference.ptx`, créé `OA00000001.wav` et `target3_01_ALIGNED`; la sortie `target4_consolidated_otto.ptx` s'est ouverte correctement dans Pro Tools. Ce résultat valide le prétraitement par consolidation, pas l'écriture directe du layout hérité. Le témoin consolidé n'avait pas de handles supplémentaires; une consolidation élargie puis retrimmée reste à confirmer séparément si des handles sont requis.
- L’audit fonctionnel et structurel général est terminé; la nouvelle voie générique de construction de session audio est validée de bout en bout dans Pro Tools avec la sortie corrigée `08`.
- La suite automatisée compte 193 tests et passe intégralement. Elle inclut les préflights de relink parent/virtuel, dont le rejet sans mutation d'un header Premiere `0x2106` de 173 octets, la lecture des occurrences de Clip Groups et les deux profils du builder.
- Toutes les écritures ont été ouvertes et vérifiées manuellement dans Pro Tools à partir des sessions de référence. Le test `05` a confirmé les WAV BWF vierges. Le test `06` a ouvert avec les deux régions, puis sa sauvegarde a révélé un lien média fixe dupliqué. Le test corrigé `08` s'est ouvert, a joué, s'est sauvegardé et s'est rouvert normalement sans alerte; les deux médias sont restés distincts.
- `README.md`, `pt_format_specs.md`, `architecture.md`, `changelog.md` et ce handoff sont resynchronisés avec le code.
- Le relink parent/racine, virtuel à offset nul et virtuel à offset non nul **natif Pro Tools** est validé de bout en bout. Ces validations ne s'étendent pas aux définitions virtuelles importées de Premiere. Les libellés de catalogue (`VIDEO`, `Exports`, etc.) sont des métadonnées PTX, jamais des chemins sur disque.
- Une seconde production OttoAlign2 de 71 médias utilise des suffixes nuls dans tout son catalogue `0x103a`; un seul `0x1001` de 31 octets y contient un faux bloc vide. Après réassemblage, 226 des 236 placements ont été relinkés, 10 ont été ignorés normalement, le catalogue final compte 297 médias, tous les WAV sont présents et la sauvegarde no-op est bit-perfect.
- `build_audio_session()` transforme désormais un template natif compatible en livraison PTX/`Audio Files` à partir d'un manifeste ordonné explicite. Chaque descripteur choisit son WAV et sa piste existante, avec filename physique, nom de clip et placement facultatifs. L’API n’impose aucune convention de nommage, de tri, de regroupement ou de nombre de pistes. Le placement BWF est utilisé par défaut et les WAV restent byte-for-byte identiques.
- Les bases comparatives originales de l’utilisateur sont conservées localement et ignorées par Git. Les sorties diagnostiques `05` à `08`, les builds et les caches ont été supprimés après validation; leurs résultats causaux sont consignés ci-dessous.
- `handoff.md` est versionné avec le projet afin que chaque clone dispose de l’état de reprise courant.

## Sources de vérité

Pour toute future révision, utiliser ces sources selon leur rôle :

1. `pt_api.py` : comportement réellement exécuté.
2. `pt_format_specs.md` : spécification normative du format, des algorithmes, des validations et des erreurs.
3. `tests/` : contrat exécutable et protection contre les régressions.
4. `README.md` : API publique, exemples, capacités et limitations destinés aux utilisateurs.
5. `architecture.md` : survol global des composants, du flux de données et des invariants.
6. `changelog.md` : historique des changements publiables.
7. `handoff.md` : état de reprise, risques connus et procédure de révision.

Le code et `pt_format_specs.md` ne doivent jamais se contredire. Une nouvelle découverte binaire doit être étayée par une comparaison de sessions, traduite en tests, puis documentée dans la spécification.

## Carte rapide du dépôt

| Élément | Rôle |
|---|---|
| `pt_api.py` | Module public, parseur, modèle de session, mutations et sauvegarde |
| `tests/` | Tests unitaires, cas malformés, ambiguïtés et restaurations transactionnelles |
| `README.md` | Documentation publique |
| `pt_format_specs.md` | Référence technique détaillée |
| `architecture.md` | Vue d’ensemble de l’architecture logicielle |
| `changelog.md` | Historique des versions |
| `pyproject.toml` | Métadonnées et construction du paquet |
| `test_session*.ptx` | Sessions réelles locales de référence, ignorées par Git |

Le projet est un module Python autonome, compatible Python 3.8+, sans dépendance d’exécution externe.

## État validé

Les contrôles suivants ont été réussis sur la version courante :

- 193 tests automatisés, y compris les entrées malformées, les restaurations après erreur, le relink parent/virtuel de production, le préflight lecture seule du header Premiere variable, le remplacement PCM compatible, les deux suffixes de nom physique `EVAW`/nul, la queue média hiérarchique à plusieurs niveaux, les faux blocs dans les identités `0x1001` et enregistrements fixes `0x2629`, la non-duplication du lien média après réassemblage, un index média supérieur à 255, l'inspection WAVE_EXTENSIBLE float, les deux profils stricts du builder, l’ordre explicite des descripteurs, les overrides de noms et de placement, les overlaps, le rejet d'une timeline cachée non vide, le ciblage de trois pistes, la lecture répétée de Clip Groups et la construction complète sur le corpus natif;
- exécution de la suite avec les avertissements Python traités comme des erreurs;
- compatibilité syntaxique Python 3.8;
- métadonnées PEP 517 alignées sur `1.4.0`; la construction du wheel `pt_api-1.4.0-py3-none-any.whl` reste à exécuter dans l'environnement de release équipé de `build`/`wheel` (ces outils ne sont pas installés sur cette station);
- absence de sortie parasite sur `stdout` dans l’API;
- sauvegarde sans modification bit-perfect sur neuf bases/références réelles :
  - `test_session.ptx`;
  - `test_session_w_audio/test_session_w_audio.ptx`;
  - `test_session_w_clip groups/test_session_w_clip groups.ptx`;
  - `test_session_w_clip groups/test_session_w_clip groups UNGROUPED.ptx`;
  - `test_6min_audio/test_6min_audio.ptx`;
  - `test_6min_audio/test_6min_audio_w_split.ptx`;
  - `test_6min_audio/test_6min_audio_w_trim.ptx`;
  - `Y:\OttoAlign2\test_sessions_original\reference.ptx` (référence de production d'environ 42 minutes);
  - `Y:\OttoAlign2\test_sessions_original\target.ptx` (deux pistes cibles de production).

La lecture de production OttoAlign2 retourne 135 régions de référence et 404 placements audio cibles. Le préflight rend 388 placements admissibles et en ignore 16 dont le chevauchement est inférieur à 0,5 seconde. Le traitement complet a produit 388 WAV indépendants, conservé les 404 placements, résolu un clip placé deux fois avec les noms `_ALIGNED`/`_ALIGNED_2`, puis réussi l'ouverture et l'écoute dans Pro Tools. La session finale se recharge et se sauvegarde sans modification de façon bit-identique. Ces actifs externes sont ignorés par Git et ne font pas partie du paquet.

Les validations manuelles dans Pro Tools ont couvert notamment : chargement/sauvegarde, mute, déplacement, duplication, renommage, split court, split d’un clip 48 kHz de six minutes à dix secondes, trims courts de début et de fin, trims combinés, Start Trim du clip long jusqu’à `10:05:50:00`, fades linéaires et equal-power, crossfade equal-power, Clip Gain, automation de volume, markers, dissolution d’un Clip Group simple, relinks parent et virtuels, remplacement PCM rendu et traitement OttoAlign2 complet.

Les essais v1 à v5 ont progressivement éliminé plusieurs divergences réelles dans les timestamps, UUID et métadonnées BWF, mais la cause structurelle décisive était ailleurs : dans la queue minimale `0x103a`, le writer augmentait les deux compteurs initiaux après insertion d'un nom, mais pas le compteur du nœud `SHARE TO NETWORK`. Le premier diagnostic A/B, même avec les blocs et le WAV natifs, a donc bloqué. La variante strictement identique sauf ce compteur `2→3` s'est ouverte correctement, confirmant causalement le défaut. La v6 a ensuite validé le chemin complet avec WAV généré : ouverture normale, premier placement toujours lié à `Audio 1_01.wav` et second placement lié à `Audio 1_08.wav`. Le parseur généralise maintenant cette règle à `K` nœuds; la session OttoAlign2 possède trois compteurs de nœud `178/179/180` et deux compteurs initiaux `182/181` pour 177 fichiers.

## Sessions de référence

- `test_session.ptx` : session de base et cycle de sauvegarde.
- `test_session_w_audio/test_session_w_audio.ptx` : session 23,976 fps contenant une piste et un fichier audio.
- `test_session_w_audio/test_session_w_audio FADES IN OUT reference.ptx` : fades créés dans Pro Tools.
- `test_session_w_audio/test_session_w_audio CROSSFADE REFERENCE.ptx` : crossfade créé dans Pro Tools.
- `test_session_w_clip groups/test_session_w_clip groups.ptx` : trois fichiers audio, les deux premiers groupés.
- `test_session_w_clip groups/test_session_w_clip groups UNGROUPED.ptx` : session comparative sans Clip Group.
- `test_6min_audio/test_6min_audio.ptx` : clip continu de six minutes à 48 kHz et 23,976 fps, sans split.
- `test_6min_audio/test_6min_audio_w_split.ptx` : même clip séparé dans Pro Tools à `10:00:10:00`; référence du sélecteur de longueur UInt32 `0x40`.
- `test_6min_audio/test_6min_audio_w_trim.ptx` : même clip Start Trimmé dans Pro Tools jusqu’à `10:05:50:00`; référence du flag d’offset source UInt32 `0x4001` (`01 40 30`).
- `relink_before/relink_before.ptx` : deux placements partageant `Audio 1_01.wav`; base comparative du relink physique, ensuite resauvegardée sans modification dans Pro Tools pour contrôler les champs volatils d'un Save As ordinaire.
- `relink_before/relink_after.ptx` : session Pro Tools où le second placement a été relinké vers `Audio 1_02.wav`.
- `virtual_relink_before/` : paire comparative de deux placements virtuels sur deux médias et références après relink gauche/droite; source de la validation des offsets virtuels nuls et non nuls.
- `pfx_import_reference/00_template` : modèle initial à deux pistes et média Pro Tools classique; il n'est pas le prototype du builder float.
- `pfx_import_reference/01_A_imported_cliplist` : template structurel du builder, avec un média float BWF importé dans la Clip List et aucune région sur la timeline.
- `pfx_import_reference/02_A_spotted_PFX01` : placement natif du premier média selon sa référence temporelle BWF.
- `pfx_import_reference/03_B_imported_cliplist` : ajout natif comparatif du second média au catalogue et à la Clip List.
- `pfx_import_reference/04_B_spotted_PFX02` : événement natif comparatif sur une autre playlist.
- `pfx_import_reference/Source PFX` : WAV A/B vierges du corpus. Pro Tools en conserve tous les chunks et échantillons, ajuste la taille RIFF puis ajoute `DGDA`, `minf` et `regn`.
- Validation historique `05` — sortie supprimée : copie du PTX natif déjà spotté de `02`, dont seul le WAV enrichi par Pro Tools avait été remplacé par sa source BWF vierge byte-for-byte. Ce cas a confirmé l'acceptation des sources sans `DGDA`/`minf`/`regn`, indépendamment du writer PTX.
- Validation historique `06` — sortie supprimée : première sortie ouverte avec deux régions, puis sauvegardée par Pro Tools. Le message `Adjusted` sur le second clip a conduit au diagnostic : les deux définitions contenaient 208 octets après `0x4403` au lieu du record `media_link` natif de 104 octets. Pro Tools avait supprimé le second média du catalogue et relié son clip au premier.
- Validation historique `07` — sortie supprimée : reproduction pré-correction de `06`, utilisée pour confirmer les deux records fautifs de 208 octets.
- Validation historique `08` — sortie supprimée après réussite : les deux définitions possédaient avant et après sauvegarde une identité de 48 octets, un lien média de 104 octets et les IDs/index physiques `0`/`1`. Les positions `1 824 829 006`/`1 826 474 650`, les longueurs `1 645 645`/`4 038 035`, les deux filenames physiques et l'overlap exact d'un échantillon ont été conservés lors du cycle Pro Tools.

Les noms historiques de ce dossier et de certaines pistes décrivent uniquement le corpus comparatif fourni. Ils ne font partie ni du contrat public, ni d’une convention reconnue par le builder.

Les contrôles manuels `05` et `08` sont réussis. La comparaison sémantique du témoin et de la sauvegarde native `08` confirme les deux entrées de catalogue, les deux longueurs, les records 48/104, les IDs/index `0`/`1`, les timestamps et l'overlap d'un échantillon. Aucun fichier d'alerte n'a été produit et les deux WAV conservent leurs SHA-256 sources.

Les fichiers préfixés par `._` sont des fichiers AppleDouble et ne sont pas des sessions PTX. Les sorties de validation peuvent être régénérées; seules les bases et paires comparatives originales doivent être conservées localement.

## Invariants à préserver

- Valider l’enveloppe PTX avant le déchiffrement XOR et avant le parseur de blocs.
- Interpréter les charges utiles prises en charge en little-endian seulement.
- Exclure le bloc racine initial `0x0001` des recherches métier.
- Garder les blocs de type fixe plats et préserver les octets bruts inconnus.
- Ne jamais ajouter de padding implicite à la sérialisation.
- Préserver `original_offset` lors d’une simple réécriture; l’effacer seulement pour les nouveaux blocs clonés.
- Pour une suppression réelle, collecter les anciens offsets puis purger les références `0x0002` correspondantes.
- Conserver le bloc `0x0002` unique, final et plat, avec ses compteurs et cibles cohérents.
- Maintenir des espaces d’identifiants séparés pour l’audio, les Clip Groups et les fades.
- `get_timeline_clip_groups()` est le lecteur en lecture seule des macros de groupe visibles : il associe la queue `00 00 01` aux définitions ordinales `0x262c`, retourne chaque occurrence avec piste, échantillons et timecodes, et reste volontairement distinct de `get_timeline_clips()`.
- Exiger un ciblage non ambigu pour les noms et placements employés par une mutation.
- Encadrer toute mutation composée et toute sauvegarde par une transaction en mémoire.
- Produire la sortie par remplacement atomique et conserver la session en mémoire intacte en cas d’échec.
- Vérifier une sauvegarde sans modification sur les neuf bases/références énumérées ci-dessus après tout changement du parseur, des offsets, des pointeurs, du chiffrement ou de la sérialisation.

## Points sensibles connus

Ces points sont documentés et ne doivent pas être « simplifiés » sans nouvelle preuve binaire :

- Le writer sait créer un fade autonome intérieur, mais `get_timeline_clips()` ne réassocie actuellement que les fades placés aux frontières d’un clip. Une révision devrait soit restreindre explicitement le writer, soit généraliser le lecteur à partir d’une session comparative Pro Tools.
- `split_clip()` met à jour les segments d’identité mutables de 48 octets trouvés dans le clone, mais ne vérifie pas lui-même qu’il en existe exactement un; `create_subclip()` applique cette validation. Toute harmonisation exige des tests de régression.
- Dans `0x2628`, ne pas confondre le flag UInt16 et le sélecteur qui le suit. Les familles observées sont `0x0000`/`0x0001` sans offset, `0x2000`/`0x2001` avec offset UInt16, `0x3000`/`0x3001` avec offset UInt24 et `0x4001` avec offset UInt32; le bit faible distingue parent et virtuel. Le sélecteur indépendant `0x10`/`0x20`/`0x30`/`0x40` donne une longueur UInt8/UInt16/UInt24/UInt32. Ainsi `01 30 40` est le flag `0x3001`, avec offset source UInt24 et longueur UInt32, tandis que `01 40 30` est le véritable flag `0x4001`, avec offset source UInt32 et longueur UInt24.
- `get_timeline_clips()` valide les fondus par défaut. Employer `include_fades=False` uniquement lorsqu'un consommateur a besoin des placements audio sans interpréter les géométries de fondu; les événements audio observés peuvent avoir la queue secondaire `00 01 01` ou `01 01 01`, le type `0x104f[15] == 0x03` restant discriminant.
- Le split sait produire une moitié droite longue avec le sélecteur `0x40`, mais sa coupe relative reste limitée à UInt24. Pour lever cette limite, obtenir une référence Pro Tools où le split survient après `0xFFFFFF` échantillons : le fragment gauche long demeure inconnu même si le flag `0x4001` de la moitié droite est maintenant documenté.
- `create_subclip()` et les trims savent produire offset UInt24/longueur UInt32 et offset UInt32/longueur UInt24. Ne pas inventer le layout d’un sous-clip virtuel à offset nul et longueur UInt32, ni celui où l’offset et la longueur exigent tous deux UInt32; ces deux combinaisons restent refusées.
- Le type de bloc `0x2523` est préservé, mais n’est pas interprété par l’API.
- La résolution des fichiers audio physiques utilise exactement l'index UInt32 little-endian à `0x2629+96` lorsque les catalogues `0x1004`/`0x103a` vérifiés sont cohérents; la session OttoAlign2 et un test à l'index 256 ont corrigé l'ancienne interprétation UInt8. Le lecteur conserve un repli heuristique par nom pour les autres layouts. Le relink exige la structure hiérarchique documentée et renouvelle/synchronise l'identité BWF/PTX, mais le lecteur public ne vérifie pas l'UUID BWF. Les libellés de queue (`SHARE TO NETWORK`, `VIDEO`, `Exports`, etc.) sont opaques et ne doivent jamais être joints au chemin du WAV; l'application appelante doit utiliser le dossier `Audio Files` associé au PTX.
- `relink_clip()` crée atomiquement le nouveau WAV avant que l'appelant ne sauvegarde le PTX. Une erreur interne restaure l'arbre et supprime le temporaire; une erreur ultérieure de `save()` laisse toutefois le WAV final à nettoyer par l'appelant. Ce contrat n'est validé que sur les catalogues natifs documentés.
- Pour un virtuel **natif** validé, préserver la géométrie `0x2628` vérifiée et calculer la référence média comme `placement - src_offset`. Pour un virtuel Premiere, aucune règle d'écriture ne doit être inférée : les corpus `target4_consolidated_reference` et `target4w_imported_audio` montrent que Pro Tools normalise/insère les entrées média selon une structure que l'API n'écrit pas encore.
- Les enregistrements fixes de 48 et 104 octets d'un `0x2629` peuvent contenir fortuitement un en-tête de bloc vide. Le relink doit les resérialiser autour du `0x4403` avant validation; ne pas exiger que le parseur les ait laissés dans un seul `bytearray`.
- L'identité média brute de 31 octets d'un `0x1001` peut subir le même faux découpage. La réassembler avant de remplacer `+22..+30`, puis normaliser seulement le clone en un payload brut.
- Les noms physiques ordonnés du `0x103a` existent avec un suffixe `EVAW` ou quatre octets nuls selon la session. Exiger une variante uniforme et la recopier lors de l'insertion; ne jamais normaliser arbitrairement une session vers l'autre variante.
- La création de Clip Groups n’est pas exposée. La dissolution prise en charge est limitée au cas simple : un groupe, une piste et un placement non ambigu.
- La lecture des Clip Groups n'a pas cette limite de cardinalité : elle accepte plusieurs définitions et plusieurs occurrences. La session `Y:\SHARE TO NETWORK\test_timeline_clip_groups\test_timeline_clip_groups.ptx` a confirmé deux placements distincts de `GROUP_READ_TEST` sur `Audio 1`, lus avec le même ID ordinal, leur durée native et leurs positions/timecodes respectifs.
- Seuls les PTX little-endian et les fréquences d’images explicitement listées dans `README.md` et `pt_format_specs.md` sont acceptés.
- Les nouveaux blocs de fade peuvent être acceptés par Pro Tools sans nouvel enregistrement `0x0002`; ne pas inventer de pointeur absent des références observées.
- Le builder audio préserve les WAV sources sans fabriquer `DGDA`, `minf` ou `regn`. Le PTX généré se recharge et tous les hashes audio concordent, mais seul le test manuel dédié établira si Pro Tools régénère ces chunks sans mettre le média offline.
- Le builder ne crée, ne supprime et ne renomme aucune piste. Il peut cibler n’importe quel nombre de pistes visibles existantes et laisse les autres intactes; une nouvelle capacité de gestion des pistes exigerait une paire comparative native.
- Le writer du builder est borné aux deux profils d'import `native_float_15_142` et `native_float_31_151_u32`. Le premier porte une durée UInt24; le second une durée UInt32 et deux références temporelles dans `0x2628`. La référence BWF reste UInt32 dans les deux cas. Un override de placement reste UInt64 dans l’événement et ne modifie pas cette identité média. Ne pas étendre les profils ou leurs offsets sans référence d'import Pro Tools correspondante et test manuel Pro Tools dédié.
- L'ordre, le regroupement et l'affectation des pistes sont des politiques clientes. Ne pas réintroduire dans l'API une inférence fondée sur un filename ou un usage applicatif particulier.

## Procédure d’une future révision

1. Lire la demande, puis les sections pertinentes de `pt_format_specs.md` et des tests.
2. Inspecter l’état Git et préserver tous les changements et fichiers de session appartenant à l’utilisateur.
3. Pour une structure binaire inconnue, obtenir une paire minimale avant/après créée dans Pro Tools. Si elle manque, demander cette session plutôt que d’inférer les octets.
4. Isoler les différences par type de bloc, chemin structurel, taille, offsets et références.
5. Implémenter les validations avant toute mutation; utiliser la transaction interne pour les opérations composées.
6. Ajouter les tests positifs, les entrées malformées, les ambiguïtés, les collisions et le rollback appropriés.
7. Exécuter la suite complète : `python -W error -m unittest discover -s tests`.
8. Si le binaire écrit change, produire une sortie dédiée et demander une validation précise dans Pro Tools.
9. Comparer les SHA-256 avant/après d’une sauvegarde sans modification sur les neuf bases/références réelles.
10. Vérifier Python 3.8 et reconstruire le paquet PEP 517.
11. Synchroniser la documentation :
    - détail binaire, validations et erreurs dans `pt_format_specs.md`;
    - fonctionnalité publique et limitations dans `README.md`;
    - composants ou flux globaux seulement dans `architecture.md`;
    - changement publiable dans `changelog.md`;
    - nombre de tests, nouvelles références et nouveaux risques dans `handoff.md`.

## Critère de fin d’une révision

Une révision n’est terminée que lorsque le code, la suite complète, les sessions réelles concernées et toute la documentation pertinente racontent la même chose. Une sortie qui se sérialise sans erreur Python ne suffit pas : elle doit aussi s’ouvrir dans Pro Tools et produire exactement le résultat demandé.
