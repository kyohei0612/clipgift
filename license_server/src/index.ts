/**
 * クリップギフト ライセンス認証サーバー (Cloudflare Workers)
 *
 * エンドポイント:
 *   POST /activate       — 初回アクティベーション
 *   POST /verify         — 30 日ハートビート
 *   POST /deactivate     — マシン解放
 *   POST /admin/issue    — 手動キー発行（Bearer 認証）
 *   POST /admin/revoke   — キー失効（Bearer 認証）
 *   GET  /health         — ヘルスチェック
 */

import type { Env } from "./types";
import { handleActivate } from "./handlers/activate";
import { handleVerify } from "./handlers/verify";
import { handleDeactivate } from "./handlers/deactivate";
import { handleAdminIssue, handleAdminRevoke } from "./handlers/admin";
import { errorResponse, jsonResponse } from "./utils";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // CORS preflight（アプリ側はデスクトップなので CORS 不要だが念のため）
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "POST, GET, OPTIONS",
          "access-control-allow-headers": "content-type, authorization",
          "access-control-max-age": "86400",
        },
      });
    }

    // ルーティング
    try {
      if (url.pathname === "/health" && request.method === "GET") {
        return jsonResponse({
          status: "ok",
          version: "0.1.0",
          server: "clipgift-license",
        });
      }

      if (url.pathname === "/activate" && request.method === "POST") {
        return await handleActivate(request, env);
      }

      if (url.pathname === "/verify" && request.method === "POST") {
        return await handleVerify(request, env);
      }

      if (url.pathname === "/deactivate" && request.method === "POST") {
        return await handleDeactivate(request, env);
      }

      if (url.pathname === "/admin/issue" && request.method === "POST") {
        return await handleAdminIssue(request, env);
      }

      if (url.pathname === "/admin/revoke" && request.method === "POST") {
        return await handleAdminRevoke(request, env);
      }

      return errorResponse(
        "invalid_request",
        `${request.method} ${url.pathname} は未定義のエンドポイントです`,
        404
      );
    } catch (err) {
      console.error("Unhandled error:", err);
      return errorResponse(
        "internal_error",
        "サーバー内部エラーが発生しました",
        500
      );
    }
  },
} satisfies ExportedHandler<Env>;
