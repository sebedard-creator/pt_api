# Handoff — pt_api (Phase 11 - Crack Absolu)
*Session du 2026-07-11*

## Contexte
L'objectif de cette session était de percer l'énigme de l'automation de Clip Gain. Après de nombreuses fausses pistes liées à la corruption d'un bloc global (causant la fameuse erreur "Magic ID does not match"), la structure a été entièrement disséquée et implémentée avec succès.

---

## État actuel

**Découvertes majeures et validations (Phase 9 à 11) :**
1. **L'Énigme du Clip Gain Totalement Résolue :** L'API implémente maintenant le Clip Gain de façon 100% native. Le bloc `0x2637` est une liste continue de points individuels de 30 octets, précédée d'un compteur. La liaison se fait via l'Index d'Automation situé **strictement et invariablement à l'offset `len-6`** dans le payload `0x2628` du clip (sous-clips et clips originaux confondus).
2. **Le fonctionnement réel des Fondus sur clips scindés :** 
   - L'événement de fondu dans `0x1052` doit **toujours** pointer sur l'ID du clip complet parent (racine), et non le clip découpé ! Pro Tools lit le timestamp absolu du fondu pour savoir sur quel sous-clip (alias) l'appliquer.
   - Les événements dans `0x1052` doivent être rigoureusement triés chronologiquement.
3. **Mise à jour de l'API :** `add_fade()` et `set_clip_gain()` ont été complètement réécrites. La restriction empêchant le Clip Gain sur les Whole File Clips a été levée, car ils possèdent eux aussi l'index à `len-6`.
4. **Validation complète :** `TEST_11_CLIPGAIN_INF.ptx` a prouvé que la manipulation des points d'automation et le mapping des index fonctionnent de façon chirurgicale.

## Prochaines Étapes pour Claude
1. **Tester `delete_clip_group()` spécifiquement** sur une session avec plusieurs groupes/événements pour confirmer que la purge `0x0002` fonctionne en conditions réelles.
3. **Implémenter `trim_clip_start()` et `trim_clip_end()`** : Dernière grosse brique d'édition audio restante. Il faudra comprendre comment modifier les offsets 24-bits/32-bits encapsulés à la fin du bloc `0x2628` tout en ajustant le point de départ sur la timeline dans `0x1050`.

### Fichiers Python actifs
| Fichier | Rôle |
|---------|------|
| `pt_api.py` | **(Cœur)** API principale autonome — parse, modifie, crypte et sauvegarde les sessions `.ptx` |

### Documentation permanente
| Fichier | Contenu |
|---------|---------|
| `architecture.md` | Stack, structure des blocs (`0x0002`, `0x262c`, timeline, fades), philosophie |
| `changelog.md` | Journal des modifications par version |
| `handoff.md` | Ce fichier — état et prochaines étapes |
| `pt_format_specs.md` | Spécifications binaires brutes |
