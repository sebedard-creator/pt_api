# Handoff — état au 13 août 2026

Ce document sert à reprendre le développement de `pt_api` sans perdre les connaissances acquises pendant l’audit. Il ne remplace ni la spécification technique ni les tests.

## État de reprise

- Version courante : `1.5.1`.
- **Création de Clip Groups validée** : `create_clip_group(track_name, group_name, start_samples, prototype_group_name)` est maintenant public. Il convertit une région audio visible existante, exactement ciblée, en Clip Group à partir d'un prototype natif déjà placé et à playlist interne vide. Il clone les définitions `0x262b`/`0x2423`/`0x2425`, crée la playlist interne `0x2428`, convertit l'événement visible en macro (`00 00 01`, flag `payload+18 = 1`) et ajoute les trois pointeurs `0x0002` nécessaires. Les groupes 1 seconde, durée variable et sur une piste distincte ont tous été ouverts et vérifiés dans Pro Tools le 12 août. La portée est volontairement simple : un composant, une piste, un prototype unique, une région source existante; aucune synthèse d'audio/clip, aucun groupe imbriqué, multi-piste ou avec fade interne.
- **Création de groupes vides validée** : `create_empty_clip_group(track_name, group_name, start_samples, length_samples)` écrit le profil Audio Region Group vide observé dans une session vierge, sans média, région audio ni prototype. Il est limité au profil 48 kHz / 23.976, aux IDs `0..255`, aux playlists cibles vides ou déjà composées uniquement de macros vides vérifiées, et aux listes parallèles `0x262c`/`0x2424`/`0x2426`/`0x2428` cohérentes. La validation manuelle a confirmé un premier groupe vide puis le renommage de dix pistes et 55 groupes vides répartis sur les pistes `1` à `10`.
- **Précondition de pool** : un pool de pistes nouvellement créé doit être ouvert puis sauvegardé une fois dans Pro Tools avant `rename_track()`. Le profil UI avant cette sauvegarde est désormais reconnu et refusé explicitement; tenter de le normaliser par l'API a produit un PTX invalide et ce chemin n'est pas publié.
- **Correctif de visibilité validé** : `set_visible_tracks()` accepte les suffixes UInt16 opaques variables des flags `0x251a` et ne modifie que leur octet de visibilité. Une template native à suffixes variables, limitée aux pistes demandées et enrichie d'un groupe vide, a été ouverte avec succès dans Pro Tools. Le suffixe opaque est donc préservé sans imposer de constante artificielle.
- **Correctif relink production post-v1.4.0** : le writer et son préflight reconnaissent maintenant les géométries parent de production `0x0000`, `0x2000` et `0x3000` en plus des racines/virtuels déjà couverts. Le flag d'origine, la largeur d'offset source et la référence incorporée sont conservés. Les vérifications isolées ont sauvegardé/rechargé des clips réels de chaque famille, puis un traitement complet a été confirmé réussi. Ceci ne change pas le refus explicite du relink virtuel Premiere à header variable.
- **Second profil de builder** : `build_audio_session()` choisit désormais son profil depuis le template, avant la validation des WAV. Les profils stricts sont `native_float_15_142` (parent `00 00 30 04 00`, 15/142/58, durée UInt24) et `native_float_31_151_u32` (parent `00 00 40 44 00`, 31/151/58, durée UInt32). `validate_audio_import_template()` expose ce diagnostic publiquement, sans mutation. Le profil 31/151/UInt32 possède des paires natives avant/après et des tests automatiques de construction/rechargement; toute modification future de son writer exige aussi son ouverture manuelle dédiée dans Pro Tools.
- **Blocage d'écriture Premiere, préflight sûr** : la lecture des headers `0x2106` de 169/173 octets et des marqueurs virtuels `0x3001/0x30` (`0x04`/`0x84`) est observée. Leur relink écrit n'est pas validé : des sorties ont été refusées par Pro Tools avec `End of stream encountered`, y compris un contrôle utilisant un catalogue hybride créé nativement. Un `Save Copy In…` conserve le header 173 octets, la signature Adobe Premiere et la géométrie virtuelle; il ne normalise donc pas ce cas. `get_relink_write_status()` détecte ce layout avant toute écriture et retourne `premiere_virtual_media`/173, afin qu'une application cliente conserve ces placements intacts et les signale à l'utilisateur. Cela ne constitue pas un support du relink Premiere.
- **Contournement validé** : `Consolidate Clip` dans Pro Tools transforme le clip concerné en parent natif avec header `0x2106` de 151 octets. Une sortie relinkée depuis cette session consolidée s'est ouverte correctement dans Pro Tools. Ce résultat valide le prétraitement par consolidation, pas l'écriture directe du layout hérité. Une consolidation élargie puis retrimmée reste à confirmer séparément si des handles sont requis.
- L’audit fonctionnel et structurel général est terminé; la nouvelle voie générique de construction de session audio est validée de bout en bout dans Pro Tools avec la sortie corrigée `08`.
- La suite automatisée compte 202 tests et passe intégralement. Elle inclut les préflights de relink parent/virtuel, la variante RX `0x3000/0x20/0x44/0x08`, le rejet sans mutation d'un header Premiere `0x2106` de 173 octets, la lecture et la création simple de Clip Groups, les deux profils du builder et les miroirs de visibilité des slots de pistes, dont des suffixes `0x251a` opaques variables.
- Toutes les écritures ont été ouvertes et vérifiées manuellement dans Pro Tools à partir des sessions de référence. Le test `05` a confirmé les WAV BWF vierges. Le test `06` a ouvert avec les deux régions, puis sa sauvegarde a révélé un lien média fixe dupliqué. Le test corrigé `08` s'est ouvert, a joué, s'est sauvegardé et s'est rouvert normalement sans alerte; les deux médias sont restés distincts.
- `README.md`, `pt_format_specs.md`, `architecture.md`, `changelog.md` et ce handoff sont resynchronisés avec le code.
- Le relink parent/racine, virtuel à offset nul et virtuel à offset non nul **natif Pro Tools** est validé de bout en bout. Ces validations ne s'étendent pas aux définitions virtuelles importées de Premiere. Les libellés de catalogue sont des métadonnées PTX opaques, jamais des chemins sur disque.
- Un second corpus de production utilise des suffixes nuls dans tout son catalogue `0x103a`; un seul `0x1001` de 31 octets y contient un faux bloc vide. Après réassemblage, le traitement complet a relinké les placements admissibles, conservé les autres et la sauvegarde no-op est bit-perfect.
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

- 202 tests automatisés, y compris les entrées malformées, les restaurations après erreur, le relink parent/virtuel de production, le préflight lecture seule du header Premiere variable, le remplacement PCM compatible, les deux suffixes de nom physique `EVAW`/nul, la queue média hiérarchique à plusieurs niveaux, les faux blocs dans les identités `0x1001` et enregistrements fixes `0x2629`, la non-duplication du lien média après réassemblage, un index média supérieur à 255, l'inspection WAVE_EXTENSIBLE float, les deux profils stricts du builder, l’ordre explicite des descripteurs, les overrides de noms et de placement, les overlaps, le rejet d'une timeline cachée non vide, le ciblage de trois pistes, la lecture et la création de Clip Groups, les miroirs de visibilité à suffixes opaques variables et la construction complète sur le corpus natif;
- exécution de la suite avec les avertissements Python traités comme des erreurs;
- compatibilité syntaxique Python 3.8;
- métadonnées PEP 517 alignées sur `1.5.1`; la construction du wheel `pt_api-1.5.1-py3-none-any.whl` reste à exécuter dans l'environnement de release équipé de `build`/`wheel` (ces outils ne sont pas installés sur cette station);
- absence de sortie parasite sur `stdout` dans l’API;
- sauvegarde sans modification bit-perfect sur des bases locales couvrant session vide, audio, groupes, clips longs et corpus de production. Ces actifs externes sont ignorés par Git et ne font pas partie du paquet.

La validation de production couvre la lecture de nombreuses régions, le relink de centaines de placements admissibles, les placements répétés, la création de WAV indépendants et l'ouverture/écoute complète dans Pro Tools. La session finale se recharge et se sauvegarde sans modification de façon bit-identique.

Les validations manuelles dans Pro Tools ont couvert notamment : chargement/sauvegarde, mute, déplacement, duplication, renommage, split court, split d’un clip 48 kHz de six minutes à dix secondes, trims courts de début et de fin, trims combinés, Start Trim du clip long jusqu’à `10:05:50:00`, fades linéaires et equal-power, crossfade equal-power, Clip Gain, automation de volume, markers, dissolution d’un Clip Group simple, création de Clip Group audio-backed (1 s, durée variable et autre piste), création répétée de groupes vides sur des pistes renommées, relinks parent et virtuels, remplacement PCM rendu et traitement complet de production.

Les essais comparatifs ont progressivement éliminé plusieurs divergences réelles dans les timestamps, UUID et métadonnées BWF. La cause structurelle décisive était dans la queue minimale `0x103a` : après insertion d’un nom, le writer augmentait les deux compteurs initiaux, mais pas le compteur du nœud terminal. Un diagnostic A/B a confirmé qu’incrémenter ce compteur était nécessaire. Le parseur généralise maintenant cette règle à `K` nœuds et le writer met à jour les `K` compteurs `N+1..N+K` ainsi que les deux compteurs initiaux.

## Sessions de référence

Les fixtures locales sont ignorées par Git et ne font pas partie du contrat public. Elles doivent couvrir au minimum :

- sauvegarde no-op et cycles de rechargement ;
- audio à 23,976 fps, fondus, crossfades, markers et automation ;
- Clip Groups audio-backed et vides, lecture et écriture ;
- clips longs, split et trims avec layouts UInt24/UInt32 ;
- relinks parent et virtuels, PCM rendu et catalogue média hiérarchique ;
- profils de template pris en charge par le builder audio ;
- pools de pistes et leurs variantes de visibilité natives.

Toute nouvelle fixture doit rester locale, porter un nom neutre et être décrite par son rôle de régression, plutôt que par le nom d’une application cliente, d’un partage réseau ou d’un projet de production. Les fichiers AppleDouble préfixés par `._` ne sont pas des sessions PTX. Les sorties de validation sont régénérables ; les paires comparatives originales doivent être conservées localement.

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
- La résolution des fichiers audio physiques utilise exactement l'index UInt32 little-endian à `0x2629+96` lorsque les catalogues `0x1004`/`0x103a` vérifiés sont cohérents; un corpus de production et un test à l'index 256 ont corrigé l'ancienne interprétation UInt8. Le lecteur conserve un repli heuristique par nom pour les autres layouts. Le relink exige la structure hiérarchique documentée et renouvelle/synchronise l'identité BWF/PTX, mais le lecteur public ne vérifie pas l'UUID BWF. Les libellés de queue sont opaques et ne doivent jamais être joints au chemin du WAV; l'application appelante doit utiliser le dossier `Audio Files` associé au PTX.
- `relink_clip()` crée atomiquement le nouveau WAV avant que l'appelant ne sauvegarde le PTX. Une erreur interne restaure l'arbre et supprime le temporaire; une erreur ultérieure de `save()` laisse toutefois le WAV final à nettoyer par l'appelant. Ce contrat n'est validé que sur les catalogues natifs documentés.
- Pour un virtuel **natif** validé, préserver la géométrie `0x2628` vérifiée et calculer la référence média comme `placement - src_offset`. Pour un virtuel Premiere, aucune règle d'écriture ne doit être inférée : les corpus observés montrent que Pro Tools normalise/insère les entrées média selon une structure que l'API n'écrit pas encore.
- Les enregistrements fixes de 48 et 104 octets d'un `0x2629` peuvent contenir fortuitement un en-tête de bloc vide. Le relink doit les resérialiser autour du `0x4403` avant validation; ne pas exiger que le parseur les ait laissés dans un seul `bytearray`.
- L'identité média brute de 31 octets d'un `0x1001` peut subir le même faux découpage. La réassembler avant de remplacer `+22..+30`, puis normaliser seulement le clone en un payload brut.
- Les noms physiques ordonnés du `0x103a` existent avec un suffixe `EVAW` ou quatre octets nuls selon la session. Exiger une variante uniforme et la recopier lors de l'insertion; ne jamais normaliser arbitrairement une session vers l'autre variante.
- `create_clip_group()` est exposée uniquement pour le profil simple template-driven : une macro prototype existante, exactement un composant audio source, une piste interne et une région audio existante à convertir. `create_empty_clip_group()` est son pendant distinct pour le profil vide 48 kHz/23.976 : il ne crée pas d'audio et accepte une piste cible vide ou déjà composée de macros vides vérifiées, jusqu'à 256 groupes (IDs `0..255`). La dissolution demeure limitée au cas historique simple : un groupe, une piste et un placement non ambigu.
- La lecture des Clip Groups n'a pas cette limite de cardinalité : elle accepte plusieurs définitions et plusieurs occurrences. Une session comparative a confirmé deux placements distincts du même groupe, lus avec le même ID ordinal, leur durée native et leurs positions/timecodes respectifs.
- Seuls les PTX little-endian et les fréquences d’images explicitement listées dans `README.md` et `pt_format_specs.md` sont acceptés.
- Les nouveaux blocs de fade peuvent être acceptés par Pro Tools sans nouvel enregistrement `0x0002`; ne pas inventer de pointeur absent des références observées.
- Le builder audio préserve les WAV sources sans fabriquer `DGDA`, `minf` ou `regn`. Le PTX généré se recharge et tous les hashes audio concordent, mais seul le test manuel dédié établira si Pro Tools régénère ces chunks sans mettre le média offline.
- Le builder ne crée ni ne supprime de piste. La gestion sûre des pools passe par `rename_track()` et `set_visible_tracks()`: cette dernière réécrit les deux miroirs `0x251a`, l'agrégat `0x2519` et les états `0x2589`, puis a été validée dans Pro Tools avec un pool de dix pistes où seules 1, 2, 3 et 9 étaient affichées. `get_tracks()` reste un inventaire de toutes les playlists principales, y compris les slots cachés. Avant tout renommage, un pool nouvellement créé doit avoir été sauvegardé au moins une fois par Pro Tools; le profil pré-normalisation est refusé explicitement. La création binaire de nouvelles pistes reste non publiée : les registres globaux qu'elle exige ne sont pas encore tous documentés.
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
- **Correctif relink RX, validé dans Pro Tools** : la géométrie native `0x3000 / 0x20 / 0x44 / 0x08` (offset source UInt24, longueur UInt16) est maintenant relinkable. Sur le clip `A145_Boom-Gain_61_PFX_Ready-05.A1-RX9Vd_01-01`, la référence incorporée a été vérifiée comme `référence 0x2106 + offset source`; le PTX produit avec `A145_Boom-Gain_61_PFX_R3ady-05.A1-RX9Vd_01.wav` s'est ouvert dans Pro Tools. `get_relink_write_status()` partage désormais la validation de géométrie du writer et retourne `unsupported_clip_layout` ou `unverified_clip_layout` au lieu d'un faux positif.
