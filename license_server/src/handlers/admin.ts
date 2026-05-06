/**
 * 管理エンドポイント:
 *   POST /admin/issue   — 手動キー発行
 *   POST /admin/revoke  — キー失効
 *
 * 認証: Authorization: Bearer ${ADMIN_BEARER_TOKEN}
 */

import type {
  AdminIssueRequest,
  AdminIssueResponse,
  AdminRevokeRequest,
  AdminRevokeResponse,
  Env,
  LicenseRecord,
} from "../types";
import { generateLicenseKey } from "../keys";
import {
  checkAdminAuth,
  defaultExtensionMonths,
  defaultSupportMonths,
  errorResponse,
  isoMonthsFromNow,
  isoNow,
  jsonResponse,
  parseJsonBody,
} from "../utils";

export async function handleAdminIssue(
  request: Request,
  env: Env
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }

  const body = await parseJsonBody<AdminIssueRequest>(request);
  if (
    !body ||
    !["lite", "std", "ext"].includes(body.plan) ||
    typeof body.buyer_email !== "string" ||
    typeof body.reason !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "plan / buyer_email / reason は必須です",
      400
    );
  }

  // 注文 ID 重複チェック（同一注文で 2 回キー発行を防ぐ）
  if (body.order_id) {
    const existing = await env.LICENSES.get(`order:${body.order_id}`);
    if (existing) {
      const existingData = JSON.parse(existing) as { key: string };
      return jsonResponse({
        status: "ok",
        key: existingData.key,
        note: "既存の注文に対応するキーを返しました",
      });
    }
  }

  // キー生成（HMAC 署名付き）
  const key = await generateLicenseKey(
    body.plan,
    env.PRODUCT_PREFIX,
    env.HMAC_SECRET
  );

  // サポート期間 / 拡張期間
  const supportMonths =
    body.support_months ?? defaultSupportMonths(body.plan);
  const extensionMonths =
    body.extension_months ?? defaultExtensionMonths(body.plan);

  const supportExpiresAt = isoMonthsFromNow(supportMonths);
  const extensionExpiresAt =
    extensionMonths !== null ? isoMonthsFromNow(extensionMonths) : null;

  const record: LicenseRecord = {
    key,
    plan: body.plan,
    issued_at: isoNow(),
    expires_at: null,
    status: "unactivated",
    machines: [],
    order_source: body.order_id?.startsWith("BOOTH-") ? "booth_auto" : "manual",
    order_id: body.order_id ?? null,
    buyer_email: body.buyer_email,
    support_expires_at: supportExpiresAt,
    extension_expires_at: extensionExpiresAt,
    notes: body.reason,
  };

  // KV 保存
  await env.LICENSES.put(`key:${key}`, JSON.stringify(record));
  if (body.order_id) {
    await env.LICENSES.put(
      `order:${body.order_id}`,
      JSON.stringify({ key, buyer_email: body.buyer_email })
    );
  }
  // 同一購入者の複数キーも追跡（不正検知用）
  const emailHash = await sha256Hex(body.buyer_email.toLowerCase());
  const existingEmailKeys = await env.LICENSES.get(`email:${emailHash}`);
  const emailKeyList = existingEmailKeys
    ? (JSON.parse(existingEmailKeys) as string[])
    : [];
  emailKeyList.push(key);
  await env.LICENSES.put(`email:${emailHash}`, JSON.stringify(emailKeyList));

  const response: AdminIssueResponse = {
    status: "ok",
    key,
    expires_at: null,
    support_expires_at: supportExpiresAt,
    extension_expires_at: extensionExpiresAt,
  };
  return jsonResponse(response);
}

export async function handleAdminRevoke(
  request: Request,
  env: Env
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }

  const body = await parseJsonBody<AdminRevokeRequest>(request);
  if (
    !body ||
    typeof body.key !== "string" ||
    typeof body.reason !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "key と reason は必須です",
      400
    );
  }

  const recordJson = await env.LICENSES.get(`key:${body.key}`);
  if (!recordJson) {
    return errorResponse("key_not_found", "キーが見つかりません", 404);
  }
  const record: LicenseRecord = JSON.parse(recordJson);

  // status を revoked に
  record.status = "revoked";
  record.notes = `${record.notes ?? ""}\n[REVOKED ${isoNow()}] ${body.reason}`;
  await env.LICENSES.put(`key:${body.key}`, JSON.stringify(record));

  // blacklist に登録（/activate /verify で即時拒否される）
  const revokedAt = isoNow();
  await env.LICENSES.put(
    `blacklist:${body.key}`,
    JSON.stringify({ revoked_at: revokedAt, reason: body.reason })
  );

  const response: AdminRevokeResponse = {
    status: "ok",
    revoked_at: revokedAt,
  };
  return jsonResponse(response);
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
