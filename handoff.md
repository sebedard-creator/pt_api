# Handoff — état au 15 juillet 2026

Ce document sert à reprendre le développement de `pt_api` sans perdre les connaissances acquises pendant l’audit. Il ne remplace ni la spécification technique ni les tests.

## État de reprise

- Version courante : `1.3.8`.
- L’audit fonctionnel et structurel entrepris sur cette version est terminé.
- La suite automatisée compte 166 tests et passe intégralement.
- Les principales écritures ont été ouvertes et vérifiées manuellement dans Pro Tools à partir des sessions de référence.
- `README.md`, `pt_format_specs.md`, `architecture.md` et `changelog.md` ont été resynchronisés avec le code.
- Le relink parent/racine, virtuel à offset nul et virtuel à offset non nul est validé de bout en bout dans Pro Tools. La session OttoAlign2 confirme la structure à trois niveaux internes (`VIDEO`, `Exports`, `test ottoalign`) et 177 fichiers; ces libellés sont des métadonnées PTX, jamais des chemins sur disque. Le traitement complet de production a créé 388 identités média indépendantes et la session résultante s'est ouverte et a joué correctement.
- Une seconde production OttoAlign2 de 71 médias utilise des suffixes nuls dans tout son catalogue `0x103a`; un seul `0x1001` de 31 octets y contient un faux bloc vide. Après réassemblage, 226 des 236 placements ont été relinkés, 10 ont été ignorés normalement, le catalogue final compte 297 médias, tous les WAV sont présents et la sauvegarde no-op est bit-perfect.
- Les bases comparatives originales de l’utilisateur sont conservées localement et ignorées par Git. Les sorties générées, diagnostics obsolètes, builds et caches ont été supprimés après validation.
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

- 166 tests automatisés, y compris les entrées malformées, les restaurations après erreur, le relink virtuel, le remplacement PCM compatible, les deux suffixes de nom physique `EVAW`/nul, la queue média hiérarchique à plusieurs niveaux, les faux blocs dans les identités `0x1001` et enregistrements fixes `0x2629`, et un index média supérieur à 255;
- exécution de la suite avec les avertissements Python traités comme des erreurs;
- compatibilité syntaxique Python 3.8;
- construction PEP 517 du paquet `pt_api-1.3.7-py3-none-any.whl`;
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
- `relink_clip()` crée atomiquement le nouveau WAV avant que l'appelant ne sauvegarde le PTX. Une erreur interne restaure l'arbre et supprime le temporaire; une erreur ultérieure de `save()` laisse toutefois le WAV final à nettoyer par l'appelant.
- Le relink accepte le parent/racine et les layouts virtuels de production vérifiés. Pour un virtuel, préserver la queue `0x2628` octet pour octet et calculer la référence média comme `embedded_reference - src_offset`; ne jamais remplacer cette référence par le timestamp du placement.
- Les enregistrements fixes de 48 et 104 octets d'un `0x2629` peuvent contenir fortuitement un en-tête de bloc vide. Le relink doit les resérialiser autour du `0x4403` avant validation; ne pas exiger que le parseur les ait laissés dans un seul `bytearray`.
- L'identité média brute de 31 octets d'un `0x1001` peut subir le même faux découpage. La réassembler avant de remplacer `+22..+30`, puis normaliser seulement le clone en un payload brut.
- Les noms physiques ordonnés du `0x103a` existent avec un suffixe `EVAW` ou quatre octets nuls selon la session. Exiger une variante uniforme et la recopier lors de l'insertion; ne jamais normaliser arbitrairement une session vers l'autre variante.
- La création de Clip Groups n’est pas exposée. La dissolution prise en charge est limitée au cas simple : un groupe, une piste et un placement non ambigu.
- Seuls les PTX little-endian et les fréquences d’images explicitement listées dans `README.md` et `pt_format_specs.md` sont acceptés.
- Les nouveaux blocs de fade peuvent être acceptés par Pro Tools sans nouvel enregistrement `0x0002`; ne pas inventer de pointeur absent des références observées.

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
