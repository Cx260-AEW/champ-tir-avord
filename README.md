# Champ de tir DGA TT — État des itinéraires

Site qui affiche, sur une carte, l'état (ouvert/fermé) des itinéraires publics
traversant le champ de tir DGA TT (Bourges/Avord), pour les 3 tranches
horaires et les 3 jours publiés dans le document officiel. Les statuts sont
extraits automatiquement, chaque jour, du PDF publié sur inforoute18.fr.

## Structure du projet

```
docs/                    ← contenu du site (servi par GitHub Pages)
  index.html              carte Leaflet + interface
  data/
    routes.geojson         tracés des itinéraires + polygone (fixe, ne change pas)
    status.json             statuts ouvert/fermé par jour/tranche (regénéré chaque jour)
scripts/
  update_status.py         télécharge et parse le PDF officiel
  kml_to_geojson.py        (usage ponctuel) reconvertit le KML si tu modifies la carte My Maps
.github/workflows/
  update.yml                automatise l'exécution quotidienne + republication
worker/
  worker.js                 relais public (Cloudflare Worker) pour le bouton "Forcer une mise à jour"
  README.md                 instructions de déploiement du relais
requirements.txt
```

## Mise en place (une seule fois)

1. **Crée un nouveau dépôt** sur GitHub (public ou privé) et pousse ce dossier :
   ```bash
   cd champ-tir-avord
   git init
   git add .
   git commit -m "Version initiale"
   git branch -M main
   git remote add origin https://github.com/<ton-compte>/<nom-du-depot>.git
   git push -u origin main
   ```

2. **Active GitHub Pages** :
   - Sur GitHub, va dans **Settings → Pages**
   - Sous "Build and deployment", choisis **Source : GitHub Actions**
   (le workflow fourni s'occupe de la publication — pas besoin de choisir une branche)

3. **Vérifie les permissions du workflow** :
   - **Settings → Actions → General → Workflow permissions**
   - Sélectionne **"Read and write permissions"**
   (nécessaire pour que le bot puisse committer les statuts mis à jour)

4. **Lance le workflow une première fois manuellement** :
   - Onglet **Actions** → sélectionne "Mise à jour quotidienne des statuts" → **Run workflow**
   - Après quelques dizaines de secondes, le site est en ligne à l'adresse
     `https://<ton-compte>.github.io/<nom-du-depot>/`

5. **(Optionnel) Active le bouton "Forcer une mise à jour"** :
   - Ce bouton, visible par tous les visiteurs du site, permet à n'importe
     qui de déclencher une actualisation immédiate sans jeton ni compte
   - Suis les instructions dans `worker/README.md` (déploiement d'un relais
     gratuit sur Cloudflare, ~5 minutes)
   - Sans cette étape, le bouton reste visible mais affiche un message
     indiquant qu'il n'est pas encore configuré — le reste du site
     fonctionne normalement

C'est tout. Ensuite, le workflow tourne automatiquement chaque jour à 1h UTC
(3h heure de Paris en été), relit le PDF officiel, met à jour `status.json`,
et republie le site. Le bouton public (une fois configuré) permet de
déclencher une actualisation à tout moment entre deux exécutions planifiées.

## En cas de panne du scraping

Si le format du PDF change (nouvelle mise en page, nouveaux itinéraires,
libellés modifiés), le script `update_status.py` échoue **sans écraser**
`status.json` — le site continue d'afficher la dernière donnée valide, avec
un bandeau d'avertissement si elle date de plus de 30 heures. Tu verras
l'échec dans l'onglet **Actions** du dépôt (⚠️ jaune sur l'exécution).

Pour corriger : ouvre `scripts/update_status.py`, ajuste `ROUTE_LABELS` ou
les expressions régulières selon le nouveau format, teste en local
(`python scripts/update_status.py`), puis pousse la correction.

## Si le tracé des itinéraires change

Si tu modifies la carte My Maps (nouveau tracé, nouvel itinéraire) :
1. Dans My Maps : **⋮ → Télécharger un fichier KML**
2. Remplace le fichier utilisé par `kml_to_geojson.py` (ou adapte le chemin
   dans le script), puis relance :
   ```bash
   python scripts/kml_to_geojson.py
   ```
3. Si tu ajoutes un nouvel itinéraire, pense aussi à l'ajouter dans
   `ROUTE_LABELS` dans `scripts/update_status.py`, avec le libellé exact tel
   qu'il apparaît dans le PDF et le `route_id` correspondant (champ `@id` du
   KML).
4. Committe et pousse les deux fichiers modifiés.

## Développement local

```bash
pip install -r requirements.txt
python scripts/update_status.py     # génère docs/data/status.json
cd docs && python3 -m http.server 8000
# ouvrir http://localhost:8000
```
