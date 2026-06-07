/**
 * Cloudflare Worker — clean-IP fetch proxy for thecollectivehk.com.
 *
 * Why this exists: thecollectivehk.com runs SiteGround's "Security Optimizer",
 * which serves an sgcaptcha interstitial to datacenter IPs (GitHub Actions
 * runners) on EVERY path — /wp-json/ and /feed/ alike. The block is IP-
 * reputation based, so the repost bot can't reach the feed directly from CI.
 * This Worker fetches the feed from Cloudflare's edge (a different, non-flagged
 * IP) and returns it verbatim, giving the bot a clean egress.
 *
 * Usage from the bot: GET https://<worker>.workers.dev/?url=<url-encoded feed>
 * Only ALLOWED_HOSTS may be proxied (so this can't be abused as an open proxy).
 *
 * Deploy:
 *   npm i -g wrangler
 *   wrangler login
 *   wrangler deploy            # uses wrangler.toml in this dir
 * Then set the repo secret COLLECTIVE_FEED_PROXY to:
 *   https://<worker-name>.<your-subdomain>.workers.dev/?url=
 */

const ALLOWED_HOSTS = new Set(["thecollectivehk.com", "www.thecollectivehk.com"]);

export default {
  async fetch(request) {
    const reqUrl = new URL(request.url);
    const target = reqUrl.searchParams.get("url");
    if (!target) {
      return new Response("missing ?url=", { status: 400 });
    }

    let upstream;
    try {
      upstream = new URL(target);
    } catch {
      return new Response("bad ?url=", { status: 400 });
    }
    if (upstream.protocol !== "https:" || !ALLOWED_HOSTS.has(upstream.hostname)) {
      return new Response("host not allowed", { status: 403 });
    }

    // Fetch from the edge with a browser-like UA. Cloudflare's own cache is
    // bypassed so the bot always sees fresh feed content.
    const resp = await fetch(upstream.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 " +
          "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
      },
      cf: { cacheTtl: 0, cacheEverything: false },
    });

    // Pass the body and status straight through; preserve content type.
    const body = await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") || "application/xml; charset=UTF-8",
        "X-Proxied-Status": String(resp.status),
      },
    });
  },
};
