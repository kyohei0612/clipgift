/**
 * サポート pending エンドポイント:
 *   GET  /support/pending?since=<marker>  — 未処理トリガー一覧（Bearer 認証）
 *   POST /support/processed/{id}          — トリガー処理完了マーク（Bearer 認証）
 *
 * ローカル watcher (scripts/watch_support_http.py) が 5 秒ごとに poll する。
 *
 * 【KV 節約設計（v5.1）】
 * - `support:queue_marker` キーが「最新の受信時刻」を保持する
 * - watcher は ?since=<前回 marker> を渡す
 * - marker が変わってない = 新規なし → LIST せずに 0 件返す（cheap GET 1 回のみ）
 * - marker が変わってる = 新規あり → LIST + 各 GET → 全件返す
 * これで Free 枠の 1000 lists/day 制限内に収まる（実受信回数 ~50/日）。
 */
import type { Env } from "../types";
import type { IncomingTriggerRecord } from "./incoming_mail";
import { checkAdminAuth, errorResponse, jsonResponse } from "../utils";

interface PendingListResponse {
  status: "ok";
  count: number;
  marker: string;
  triggers: IncomingTriggerRecord[];
}

export const QUEUE_MARKER_KEY = "support:queue_marker";

export async function handleSupportPending(
  request: Request,
  env: Env
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }

  const url = new URL(request.url);
  const since = url.searchParams.get("since") || "";

  // 現在の marker を読む（受信があるたびに Email Worker / handleSupportReport が更新する）
  const marker = (await env.LICENSES.get(QUEUE_MARKER_KEY)) || "";

  // marker が変わってない場合は LIST しない（cheap path）。
  // 両方空（"" === ""）も「未受信状態 = 変化なし」として cheap path に乗せる。
  // 初回起動時に既存 pending を取りこぼさないため、watcher 側は last_marker="__init__"
  // で初回呼び出しすれば必ず marker と不一致になり LIST 経路に流れる。
  if (since === marker) {
    return jsonResponse({
      status: "ok",
      count: 0,
      marker,
      triggers: [],
    } satisfies PendingListResponse);
  }

  // M-5: KV LIST のページネーション対応。
  // 1 リクエストの安全上限として 5 ページ（最大 500 件）でループを止める。
  // 100 件を超える pending が溜まるのは異常事態（通常は数件〜十数件想定）なので
  // この上限を超える分は次回 poll に持ち越す。
  const triggers: IncomingTriggerRecord[] = [];
  const PAGE_LIMIT = 100;
  const MAX_PAGES = 5;
  let cursor: string | undefined = undefined;
  let pages = 0;
  while (pages < MAX_PAGES) {
    const listOpts: KVNamespaceListOptions = {
      prefix: "support:pending:",
      limit: PAGE_LIMIT,
    };
    if (cursor) {
      listOpts.cursor = cursor;
    }
    const list: KVNamespaceListResult<unknown> = await env.LICENSES.list(
      listOpts
    );
    for (const key of list.keys) {
      const id = key.name.replace("support:pending:", "");
      const recRaw = await env.LICENSES.get(`support:incoming:${id}`);
      if (!recRaw) {
        // pending マーカーは残ってるが本体が消えている（TTL 切れ等）→ pending も消す
        await env.LICENSES.delete(`support:pending:${id}`);
        continue;
      }
      try {
        triggers.push(JSON.parse(recRaw) as IncomingTriggerRecord);
      } catch (e) {
        console.warn("Pending record parse failed:", id, e);
      }
    }
    pages++;
    if (list.list_complete) {
      break;
    }
    // list_complete=false のときのみ cursor が存在する（KV API の判別共用体）
    cursor = list.cursor;
    if (!cursor) {
      break;
    }
  }

  // 受信時刻順（古い順）でソート → 順番に処理しやすく
  triggers.sort((a, b) => a.received_at.localeCompare(b.received_at));

  const response: PendingListResponse = {
    status: "ok",
    count: triggers.length,
    marker,
    triggers,
  };
  return jsonResponse(response);
}

export async function handleSupportProcessed(
  request: Request,
  env: Env,
  triggerId: string
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }
  // M-3: UUID は 36 文字。念のため上限 40 文字とする。
  if (!triggerId || !/^[0-9a-f-]{8,40}$/i.test(triggerId)) {
    return errorResponse("invalid_request", "trigger_id が不正です", 400);
  }
  // pending マーカーだけ削除。incoming 本体は履歴として保持（TTL で自動消失）。
  await env.LICENSES.delete(`support:pending:${triggerId}`);
  return jsonResponse({ status: "ok", processed_id: triggerId });
}
