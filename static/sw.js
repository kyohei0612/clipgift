// 最小限の Service Worker
// 目的: Chrome の「アプリとしてインストール」要件を満たすため。
// キャッシュ機能は持たず、すべてのリクエストはネットワークへスルー。
// localhost サーバー前提なのでオフライン対応は不要。

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // パススルー: ブラウザの通常リクエスト経路をそのまま使う
});
