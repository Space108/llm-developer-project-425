/**
 * Cloudflare Worker: мост Telegram → Yandex API Gateway.
 * Telegram достучится до workers.dev, а Worker уже дергает YC.
 *
 * Деплой:
 *   npx wrangler deploy
 * Затем:
 *   setWebhook на https://<name>.<subdomain>.workers.dev/
 */
export default {
  async fetch(request) {
    if (request.method !== "POST") {
      return new Response("ok", { status: 200 });
    }

    const ycUrl =
      "https://d5dk0jbjel5geh3uic6k.uvah0e6r.apigw.yandexcloud.net/telegram";
    const body = await request.text();

    const ycRes = await fetch(ycUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });

    const text = await ycRes.text();
    return new Response(text, {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  },
};
