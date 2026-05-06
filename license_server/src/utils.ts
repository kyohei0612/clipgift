/**
 * 共通ユーティリティ: HTTP レスポンスヘルパー、認証、日付計算
 */

import type { Env, ErrorCode, ErrorResponse } from "./types";

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
 * プラン別のサポート期間（バグ修正期間）月数を返す
 */
export function defaultSupportMonths(
  plan: "lite" | "std" | "ext"
): number {
  switch (plan) {
    case "lite":
      return 3;
    case "std":
      return 12;
    case "ext":
      return 240; // 永続相当（20 年）
  }
}

/**
 * プラン別の拡張機能期間月数を返す（null = 拡張機能なし）
 */
export function defaultExtensionMonths(
  plan: "lite" | "std" | "ext"
): number | null {
  switch (plan) {
    case "lite":
      return null;
    case "std":
      return 12;
    case "ext":
      return 240;
  }
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
