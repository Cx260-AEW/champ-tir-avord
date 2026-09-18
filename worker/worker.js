/**
 * Relais pour déclencher le workflow GitHub "update.yml", de deux façons :
 *
 * 1. fetch (POST public, sans authentification) — utilisé par le bouton
 *    "Forcer une mise à jour" du site. N'importe quel visiteur peut
 *    l'appeler ; le Worker, lui, s'authentifie auprès de GitHub avec un
 *    jeton gardé côté serveur (jamais visible du public).
 *
 * 2. scheduled (Cron Trigger Cloudflare) — déclenchement automatique à
 *    heure fixe, en remplacement du cron GitHub Actions natif, qui n'est
 *    que "best effort" et peut prendre plusieurs heures de retard sur les
 *    dépôts publics peu chargés. Les Cron Triggers Cloudflare sont
 *    nettement plus ponctuels.
 *
 * Variables d'environnement à configurer dans Cloudflare (Settings →
 * Variables and Secrets) :
 *   GH_TOKEN          (secret, chiffré) — fine-grained PAT, portée
 *                      "Actions: Read and write" sur ce dépôt uniquement
 *   GH_OWNER           ex. "Cx260-AEW"
 *   GH_REPO            ex. "champ-tir-avord"
 *   GH_WORKFLOW_FILE   "update.yml"
 *   GH_BRANCH          "main"
 *
 * Cron Trigger à ajouter dans Cloudflare (Settings → Triggers → Cron
 * Triggers → Add) : par exemple 17 3 * * * (même syntaxe que le cron
 * GitHub Actions qu'il remplace).
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
  });
}

/**
 * Déclenche le workflow_dispatch GitHub. Renvoie { ok, ...détails } —
 * ne lève jamais d'exception, pour être appelable aussi bien depuis
 * fetch() (qui doit répondre au visiteur) que depuis scheduled() (qui
 * n'a personne à qui répondre, seulement des logs Cloudflare).
 */
async function triggerGithubWorkflow(env) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW_FILE}/dispatches`;

  let resp;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GH_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'champ-tir-avord-worker',
      },
      body: JSON.stringify({ ref: env.GH_BRANCH || 'main' }),
    });
  } catch (err) {
    return { ok: false, error: 'network', detail: String(err) };
  }

  if (resp.status === 204) {
    return { ok: true };
  }

  const detail = await resp.text();
  return { ok: false, error: 'github_error', status: resp.status, detail: detail.slice(0, 300) };
}

async function checkCooldown(env) {
  if (!env.COOLDOWN) return null; // pas de binding KV : pas de cooldown
  const cooldownSeconds = Number(env.COOLDOWN_SECONDS || 120);
  const last = await env.COOLDOWN.get('last_trigger');
  const now = Date.now();
  if (last && now - Number(last) < cooldownSeconds * 1000) {
    return Math.ceil((cooldownSeconds * 1000 - (now - Number(last))) / 1000);
  }
  await env.COOLDOWN.put('last_trigger', String(now));
  return null;
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }
    if (request.method !== 'POST') {
      return json({ ok: false, error: 'method_not_allowed' }, 405);
    }

    const retryAfter = await checkCooldown(env);
    if (retryAfter !== null) {
      return json({ ok: false, error: 'cooldown', retry_after_seconds: retryAfter }, 429);
    }

    const result = await triggerGithubWorkflow(env);
    return json(result, result.ok ? 200 : 502);
  },

  async scheduled(controller, env, ctx) {
    // Le cooldown ne s'applique pas ici : un déclenchement planifié doit
    // toujours avoir lieu, il ne doit jamais être bloqué par un visiteur
    // qui aurait cliqué le bouton juste avant.
    const result = await triggerGithubWorkflow(env);
    console.log('[scheduled]', JSON.stringify(result));
  },
};