# Handoff — état au 15 juillet 2026

Ce document sert à reprendre le développement de `pt_api` sans perdre les connaissances acquises pendant l’audit. Il ne remplace ni la spécification technique ni les tests.

## État de reprise

- Version courante : `1.3.6`.
- L’audit fonctionnel et structurel entrepris sur cette version est terminé.
- La suite automatisée compte 146 tests et passe intégralement.
- Les principales écritures ont été ouvertes et vérifiées manuellement dans Pro Tools à partir des sessions de référence.
- `README.md`, `pt_format_specs.md`, `architecture.md` et `changelog.md` ont été resynchronisés avec le code.
- Aucune validation manuelle Pro Tools n’est actuellement en attente.
- Le répertoire de travail contient des changements non validés dans Git ainsi que les fichiers de test de l’utilisateur. Ne pas les réinitialiser ni les supprimer.
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
| `test_session*.ptx` | Sessions réelles de référence et sorties de validation |

Le projet est un module Python autonome, compatible Python 3.8+, sans dépendance d’exécution externe.

## État validé

Les contrôles suivants ont été réussis sur la version courante :

- 146 tests automatisés, y compris les entrées malformées et les restaurations après erreur;
- exécution de la suite avec les avertissements Python traités comme des erreurs;
- compatibilité syntaxique Python 3.8;
- construction PEP 517 du paquet `pt_api-1.3.6-py3-none-any.whl`;
- absence de sortie parasite sur `stdout` dans l’API;
- sauvegarde sans modification bit-perfect sur les quatre bases réelles suivantes :
  - `test_session.ptx`;
  - `test_session_w_audio/test_session_w_audio.ptx`;
  - `test_session_w_clip groups/test_session_w_clip groups.ptx`;
  - `test_session_w_clip groups/test_session_w_clip groups UNGROUPED.ptx`.

Les validations manuelles dans Pro Tools ont couvert notamment : chargement/sauvegarde, mute, déplacement, duplication, renommage, split, start trim, end trim, trims combinés, fades linéaires et equal-power, crossfade equal-power, Clip Gain, automation de volume, markers et dissolution d’un Clip Group simple.

## Sessions de référence

- `test_session.ptx` : session de base et cycle de sauvegarde.
- `test_session_w_audio/test_session_w_audio.ptx` : session 23,976 fps contenant une piste et un fichier audio.
- `test_session_w_audio/test_session_w_audio FADES IN OUT reference.ptx` : fades créés dans Pro Tools.
- `test_session_w_audio/test_session_w_audio CROSSFADE REFERENCE.ptx` : crossfade créé dans Pro Tools.
- `test_session_w_clip groups/test_session_w_clip groups.ptx` : trois fichiers audio, les deux premiers groupés.
- `test_session_w_clip groups/test_session_w_clip groups UNGROUPED.ptx` : session comparative sans Clip Group.

Les fichiers préfixés par `._` sont des fichiers AppleDouble et ne sont pas des sessions PTX. Les sorties portant `API`, `fixed` ou un suffixe de version sont des preuves de validation; ne pas les substituer silencieusement aux bases ou aux références.

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
- Vérifier une sauvegarde sans modification sur les quatre sessions de base après tout changement du parseur, des offsets, des pointeurs, du chiffrement ou de la sérialisation.

## Points sensibles connus

Ces points sont documentés et ne doivent pas être « simplifiés » sans nouvelle preuve binaire :

- Le writer sait créer un fade autonome intérieur, mais `get_timeline_clips()` ne réassocie actuellement que les fades placés aux frontières d’un clip. Une révision devrait soit restreindre explicitement le writer, soit généraliser le lecteur à partir d’une session comparative Pro Tools.
- `split_clip()` met à jour les segments d’identité mutables de 48 octets trouvés dans le clone, mais ne vérifie pas lui-même qu’il en existe exactement un; `create_subclip()` applique cette validation. Toute harmonisation exige des tests de régression.
- Le split utilise des fenêtres source spécialisées de 32 bits, alors que le décodeur public normalise certains champs `0x0000`/`0x0001` sur 24 bits. Ne pas fusionner ces chemins sans session comparative.
- Le type de bloc `0x2523` est préservé, mais n’est pas interprété par l’API.
- La résolution des fichiers audio physiques repose sur des heuristiques de nom; l’UUID BWF n’est pas exploité.
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
9. Comparer les SHA-256 avant/après d’une sauvegarde sans modification sur les quatre bases réelles.
10. Vérifier Python 3.8 et reconstruire le paquet PEP 517.
11. Synchroniser la documentation :
    - détail binaire, validations et erreurs dans `pt_format_specs.md`;
    - fonctionnalité publique et limitations dans `README.md`;
    - composants ou flux globaux seulement dans `architecture.md`;
    - changement publiable dans `changelog.md`;
    - nombre de tests, nouvelles références et nouveaux risques dans `handoff.md`.

## Critère de fin d’une révision

Une révision n’est terminée que lorsque le code, la suite complète, les sessions réelles concernées et toute la documentation pertinente racontent la même chose. Une sortie qui se sérialise sans erreur Python ne suffit pas : elle doit aussi s’ouvrir dans Pro Tools et produire exactement le résultat demandé.
