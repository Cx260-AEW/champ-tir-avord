/**
 * Relais public pour déclencher le workflow GitHub "update.yml" sans
 * exposer aucun secret au navigateur. N'importe quel visiteur du site
 * peut appeler cette URL (POST, sans authentification) ; le Worker,
 * lui, s'authentifie auprès de GitHub avec un jeton gardé côté serveur
 * (variable d'environnement chiffrée, jamais visible du public).
 *
 * Variables d'environnement à configurer dans Cloudflare (Settings →
 * Variables and Secrets) :
 *   GH_TOKEN          (secret, chiffré) — fine-grained PAT, portée
 *                      "Actions: Read and write" sur ce dépôt uniquement
 *   GH_OWNER           ex. "Cx260-AEW"
 *   GH_REPO            ex. "champ-tir-avord"
 *   GH_WORKFLOW_FILE   "update.yml"
 *   GH_BRANCH          "main"
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

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }
    if (request.method !== 'POST') {
      return json({ ok: false, error: 'method_not_allowed' }, 405);
    }

    // Cooldown global simple (facultatif) : si une variable KV nommée
    // COOLDOWN est liée à ce Worker, on évite de redéclencher plus d'une
    // fois toutes les COOLDOWN_SECONDS secondes, tous visiteurs confondus.
    // Sans binding KV configuré, cette protection est simplement ignorée.
    if (env.COOLDOWN) {
      const cooldownSeconds = Number(env.COOLDOWN_SECONDS || 120);
      const last = await env.COOLDOWN.get('last_trigger');
      const now = Date.now();
      if (last && now - Number(last) < cooldownSeconds * 1000) {
        const retryAfter = Math.ceil((cooldownSeconds * 1000 - (now - Number(last))) / 1000);
        return json({ ok: false, error: 'cooldown', retry_after_seconds: retryAfter }, 429);
      }
      await env.COOLDOWN.put('last_trigger', String(now));
    }

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
      return json({ ok: false, error: 'network', detail: String(err) }, 502);
    }

    if (resp.status === 204) {
      return json({ ok: true });
    }

    const detail = await resp.text();
    return json({ ok: false, error: 'github_error', status: resp.status, detail: detail.slice(0, 300) }, 502);
  },
};
