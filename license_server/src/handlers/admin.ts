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
  // 後方互換: "single" / "lite" / "std" / "ext" すべて受理（内部的には single として発行）
  const planAccepted = body
    ? ["single", "lite", "std", "ext"].includes(body.plan as string)
    : false;
  if (
    !body ||
    !planAccepted ||
    typeof body.buyer_email !== "string" ||
    typeof body.reason !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "plan / buyer_email / reason は必須です",
      400
    );
  }
  // すべて single に正規化
  body.plan = "single";

  // 注文 ID 重複チェック（同一注文で 2 回キー発行を防ぐ）
  if (body.order_id) {
    const existing = await env.LICENSES.get(`order:${body.order_id}`);
    if (existing) {
      // M-6: JSON.parse 失敗時に 500 で巻き込まないよう try/catch で防御。
      // 既存データが破損している場合は 400（不正データ）として明示的に返し、
      // 上書き発行は安全側でブロックする（手動修復に倒す）。
      try {
        const existingData = JSON.parse(existing) as { key: string };
        return jsonResponse({
          status: "ok",
          key: existingData.key,
          note: "既存の注文に対応するキーを返しました",
        });
      } catch (e) {
        console.warn(
          `order:${body.order_id} の既存データが不正な JSON です:`,
          e
        );
        return errorResponse(
          "invalid_request",
          `既存の注文データが破損しています（order_id=${body.order_id}）。手動で確認してください。`,
          400
        );
      }
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
  //
  // ⚠️ ここは「キーを KV に保存し終えた後」なので、例外を投げてはいけない。
  // 旧コードは JSON.parse が無防備で、email リストが壊れていると 500 になった。
  // 呼び出し側（scripts/issue_license.py）は失敗と判断して再実行し、
  // order_id なしの手動発行では重複チェックも効かないため **キーが二重発行される**。
  // 追跡情報は補助データなので、壊れていても発行そのものは成功させる。
  try {
    const emailHash = await sha256Hex(body.buyer_email.toLowerCase());
    const existingEmailKeys = await env.LICENSES.get(`email:${emailHash}`);
    let emailKeyList: string[] = [];
    if (existingEmailKeys) {
      try {
        const parsed = JSON.parse(existingEmailKeys);
        if (Array.isArray(parsed)) {
          emailKeyList = parsed as string[];
        } else {
          console.warn(`email:${emailHash} が配列ではありません。作り直します。`);
        }
      } catch (e) {
        console.warn(`email:${emailHash} の JSON が壊れています。作り直します:`, e);
      }
    }
    emailKeyList.push(key);
    await env.LICENSES.put(`email:${emailHash}`, JSON.stringify(emailKeyList));
  } catch (e) {
    console.warn("購入者インデックスの更新に失敗（キー発行自体は成功）:", e);
  }

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
  // handleAdminIssue 側（M-6）は既に try/catch を入れてあるのに、ここだけ素通しで
  // レコード破損時に 500 になっていた。失効は「壊れたレコードでも通したい」操作なので、
  // パースできなくても blacklist 登録だけは必ず実行する。
  let record: LicenseRecord | null = null;
  try {
    record = JSON.parse(recordJson) as LicenseRecord;
  } catch (e) {
    console.warn(`key:${body.key} のレコードが壊れています（blacklist のみ登録）:`, e);
  }

  // status を revoked に（レコードが読めた場合のみ）
  if (record) {
    record.status = "revoked";
    record.notes = `${record.notes ?? ""}\n[REVOKED ${isoNow()}] ${body.reason}`;
    await env.LICENSES.put(`key:${body.key}`, JSON.stringify(record));
  }

  // blacklist に登録（/activate /verify で即時拒否される）
  // ここが失効の実効部分なので、レコードの状態に関わらず必ず実行する
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
