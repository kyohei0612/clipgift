/**
 * 共通ユーティリティ: HTTP レスポンスヘルパー、認証、日付計算
 */

import type { BillingType, Env, ErrorCode, ErrorResponse, LicenseRecord } from "./types";

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

export function errorResponse(
  code: ErrorCode,
  message: string,
  status: number,
  hint?: string
): Response {
  const body: ErrorResponse = { status: "error", code, message };
  if (hint !== undefined) {
    body.hint = hint;
  }
  return jsonResponse(body, status);
}

/**
 * Bearer トークン認証（管理エンドポイント用）
 */
export function checkAdminAuth(request: Request, env: Env): boolean {
  const authHeader = request.headers.get("authorization");
  if (!authHeader) {
    return false;
  }
  const match = authHeader.match(/^Bearer\s+(.+)$/i);
  if (!match) {
    return false;
  }
  // タイミング攻撃を避けるため定数時間比較
  return constantTimeEq(match[1], env.ADMIN_BEARER_TOKEN);
}

function constantTimeEq(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

/**
 * 現在時刻 + N ヶ月の ISO 文字列を返す
 */
export function isoMonthsFromNow(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() + months);
  return d.toISOString();
}

/**
 * 現在時刻 + N 日の ISO 文字列を返す
 */
export function isoDaysFromNow(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString();
}

/**
 * 現在時刻の ISO 文字列
 */
export function isoNow(): string {
  return new Date().toISOString();
}

/**
 * Stripe の unix 秒（number）を ISO 文字列に変換。
 * 不正値（未定義・NaN）の場合は null を返す。
 */
export function isoFromUnixSeconds(seconds: unknown): string | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
    return null;
  }
  return new Date(seconds * 1000).toISOString();
}

/**
 * レコードの課金形態を正規化して返す。
 * 既存の買い切りレコード（billing_type 未設定）は "perpetual" として扱う。
 * ★ サブスク判定は必ずこの関数を通すこと（undefined を取りこぼさないため）。
 */
export function billingTypeOf(record: LicenseRecord): BillingType {
  return record.billing_type ?? "perpetual";
}

/**
 * Phase 1 = single プラン 1 種のみ。サポート / 拡張ともに 240 ヶ月（永久相当）。
 *
 * 引数は後方互換のため "single" 以外も受け取れるが、すべて同じ値を返す。
 */
export function defaultSupportMonths(_plan: string): number {
  return 240;
}

export function defaultExtensionMonths(_plan: string): number | null {
  return 240;
}

/**
 * リクエストボディを JSON として安全にパース
 */
export async function parseJsonBody<T>(
  request: Request
): Promise<T | null> {
  try {
    const text = await request.text();
    if (!text) {
      return null;
    }
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}
