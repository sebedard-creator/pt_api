# Document de Passation (Handoff) - Projet Pro Tools API

## État Actuel (Fin de Session)
L'audit technique de l'API (`audit.md`) est officiellement clôturé. L'API est dans un état stable et sain (v0.7.0).

**Accomplissements majeurs de la session :**
- Réparation des bugs bloquants (`add_crossfade`, `duplicate_clip`).
- Correction de la confusion architecturale entourant le bloc `0x2077` (qui était confondu avec le cache de fondu, mais qui est en réalité le marqueur).
- Uniformisation de la résolution d'ID (`clip_id` ordinal) dans toutes les fonctions (`mute_clip`, `move_clip`, etc.).
- Stabilisation de la validation des en-têtes de clips pour autoriser les clips parents pré-scindés (`01 00`).
- Sécurisation du `TimecodeEngine` contre les frame rates inconnus et prévention de corruption de géométrie de fondu (24-bit overlaps).
- Implémentation du support universel UTF-8 pour supporter les caractères québécois (accents).
- Nettoyage rigoureux de la dette technique (suppression de 5x `find_blocks()`, retrait d'imports redondants, retrait de `serialize()`, ajout de l'auto-incrémentation pour `add_marker()`).

**Bugs connus :**
- Aucun bug critique ou majeur identifié à l'heure actuelle.

## Prochaines Étapes Exactes
1. **Implémentation de `trim_clip_start()` et `trim_clip_end()` :**
   - Cette fonctionnalité a été demandée par l'utilisateur mais mise en attente durant le nettoyage de l'audit.
   - Elle nécessitera un plan d'implémentation car elle touche à la modification des `source_offset` et des `length` des clips (`0x2628`).
2. **Investigation de la structure `0x262c` (Clip Groups) :**
   - La création depuis zéro de Clip Groups reste en attente en raison de la complexité des blocs `0x2428` et `0x2501` liés à la timeline.

## Notes pour l'Agent Suivant
- L'environnement est strict : priorité totale au back-end, tolérance zéro pour les secrets en dur, et obligation de laisser un code minimaliste (pas de fonctions mortes ou de try/except nus).
- Les modifications de l'API sont validées en mémoire mais doivent faire l'objet de scripts de validation locaux (ex: `test_tech_debt.py`) pour prouver qu'elles ne causent aucune régression avant d'être commises.
