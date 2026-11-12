# Page de graphiques du suivi STAKU

`build.py` lit les quatre bases Notion du dossier (Tâches, Feuille de route, Catalogue, Risques et décisions), calcule les indicateurs et écrit une page HTML statique. Le workflow `.github/workflows/dashboard.yml` la régénère toutes les heures et la publie sur GitHub Pages. La page est ensuite intégrée dans Notion avec un bloc `/embed`.

La page ne contient que des comptes et des libellés (noms de jalons, de catégories, codes de risques). Elle ne contient ni le GDD ni le texte des tâches.

## Mise en place, une seule fois

1. **Créer la clé Notion.** Sur https://www.notion.so/profile/integrations, créer une intégration interne « STAKU graphiques » avec le seul droit de lecture, et copier son secret.
2. **Donner accès au dossier.** Dans Notion, ouvrir « STAKU · Dossier patient », menu `•••`, Connexions, ajouter « STAKU graphiques ».
3. **Enregistrer la clé dans GitHub.** Dans le dépôt : Settings, Secrets and variables, Actions, New repository secret, nom `NOTION_TOKEN`.
4. **Activer GitHub Pages.** Settings, Pages, Source : « GitHub Actions ».
5. **Lancer une première fois.** Onglet Actions, « Graphiques du suivi », Run workflow.

## Tester sans Notion

    python3 build.py --sample sample.json --out /tmp/site

`sample.json` est un jeu d'essai : ses comptes reprennent l'état du 12/11/2026, son historique est inventé pour tester la courbe.

## Limites

- GitHub suspend les workflows planifiés après 60 jours sans activité sur le dépôt.
- Ce dépôt et sa page sont publics : toute personne ayant le lien les voit. Le dépôt du jeu, lui, reste privé.
- La courbe d'historique gagne un point par jour. Elle relit le fichier `history.json` déjà publié, donc elle repart de zéro si la page est dépubliée.
