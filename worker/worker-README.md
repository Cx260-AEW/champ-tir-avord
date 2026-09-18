# Relais public de déclenchement (Cloudflare Worker)

Ce petit service gratuit a deux rôles :

1. Permettre à **n'importe quel visiteur du site** de déclencher une mise
   à jour manuelle, sans qu'aucun jeton GitHub ne soit jamais exposé au
   public
2. Déclencher la mise à jour **automatique quotidienne** à heure fixe, de
   façon nettement plus fiable que le cron natif de GitHub Actions (voir
   [étape 8](#8-remplace-le-cron-github-actions-par-un-cron-trigger-cloudflare-recommandé))

Le jeton GitHub reste dans les deux cas uniquement dans la configuration
privée de Cloudflare.

## Mise en place (une seule fois, ~5 minutes)

1. **Crée un compte Cloudflare** (gratuit) sur https://dash.cloudflare.com/sign-up
   si tu n'en as pas déjà un.

2. **Crée un Worker** :
   - Dans le tableau de bord Cloudflare : **Workers & Pages → Create → Create Worker**
   - Donne-lui un nom, ex. `champ-tir-update` (l'URL sera
     `https://champ-tir-update.<ton-sous-domaine>.workers.dev`)
   - Clique **Deploy** pour créer la coquille vide

3. **Colle le code** :
   - Une fois le Worker créé, clique **Edit code**
   - Remplace tout le contenu par celui de `worker.js` (dans ce dossier)
   - Clique **Deploy**

4. **Configure les variables** :
   - Dans le Worker : **Settings → Variables and Secrets**
   - Ajoute ces variables (type "Text", sauf `GH_TOKEN` en "Secret") :
     | Nom | Valeur | Type |
     |---|---|---|
     | `GH_TOKEN` | ton fine-grained PAT (voir ci-dessous) | **Secret** |
     | `GH_OWNER` | `Cx260-AEW` | Text |
     | `GH_REPO` | `champ-tir-avord` | Text |
     | `GH_WORKFLOW_FILE` | `update.yml` | Text |
     | `GH_BRANCH` | `main` | Text |

5. **Crée le jeton GitHub** (s'il n'existe pas déjà) :
   - GitHub → **Settings (compte) → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
   - **Repository access : Only select repositories → `champ-tir-avord`**
   - **Permissions → Actions : Read and write** (rien d'autre)
   - Copie le jeton généré et colle-le dans la variable `GH_TOKEN` du Worker (étape 4)

6. **Récupère l'URL du Worker** (visible en haut de sa page Cloudflare,
   ex. `https://champ-tir-update.paul123.workers.dev`) et colle-la dans
   `docs/index.html`, à la ligne :
   ```js
   const UPDATE_WORKER_URL = 'https://REMPLACE-MOI.workers.dev';
   ```

7. **Teste** : ouvre le site déployé, clique sur "Forcer une mise à jour".
   Le workflow GitHub doit démarrer dans l'onglet Actions du dépôt en
   quelques secondes.

## 8. Remplace le cron GitHub Actions par un Cron Trigger Cloudflare (recommandé)

Le cron natif de GitHub Actions (`schedule:` dans `update.yml`) n'est
garanti par GitHub qu'en "best effort" — sur un dépôt public peu chargé,
un déclenchement prévu à 3h17 UTC peut concrètement n'avoir lieu que
plusieurs heures plus tard. Les Cron Triggers Cloudflare, eux, sont
nettement plus ponctuels (généralement à la minute près).

Comme le Worker sait déjà déclencher le workflow (c'est ce que fait le
bouton du site), il suffit de lui ajouter un déclenchement planifié :

1. Dans Cloudflare : ton Worker → **Settings → Triggers → Cron Triggers → Add Cron Trigger**
2. Renseigne l'expression cron souhaitée, par exemple `17 3 * * *` (3h17
   UTC chaque jour — la même syntaxe que le cron GitHub Actions qu'elle
   remplace)
3. Sauvegarde

Une fois ce Cron Trigger actif, retire (ou commente) le bloc `schedule:`
dans `.github/workflows/update.yml` pour éviter deux déclenchements
distincts proches l'un de l'autre — le workflow garde son
`workflow_dispatch:`, donc le bouton du site et ce Cron Trigger
continuent tous les deux de fonctionner normalement, ils appellent
simplement la même API.

## Protection anti-abus (facultative)

Comme n'importe qui peut appeler ce relais, un visiteur malveillant (ou
un simple double-clic collectif) pourrait déclencher le workflow trop
souvent. Comme le dépôt est public, les minutes GitHub Actions sont
gratuites et illimitées, donc le risque financier est nul — mais pour
éviter le bruit inutile dans l'historique Actions, tu peux ajouter un
cooldown global (ex. 2 minutes minimum entre deux déclenchements, tous
visiteurs confondus) :

1. Dans Cloudflare : **Workers & Pages → ton Worker → Settings → Bindings → Add → KV Namespace**
2. Crée un namespace KV (ex. `champ-tir-cooldown`), lie-le sous le nom **`COOLDOWN`**
3. (Optionnel) Ajoute une variable `COOLDOWN_SECONDS` (ex. `120`) pour ajuster la durée

Le code du Worker détecte automatiquement ce binding et applique le
cooldown ; sans lui, aucune limite n'est appliquée côté serveur (le
bouton du site se désactive quand même 60 secondes après un clic réussi,
côté navigateur).