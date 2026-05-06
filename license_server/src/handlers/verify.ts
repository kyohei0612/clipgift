/**
 * POST /verify — 30 日ハートビート
 *
 * クライアントが credential の next_verify_at を超えたら呼び出す。
 * サーバー側は最新の状態（プラン情報、失効状況）を反映した credential を返す。
 */

import type {
  Env,
  LicenseRecord,
  VerifyRequest,
  VerifyResponse,
} from "../types";
import { generateCredential, validateKey } from "../keys";
import {
  errorResponse,
  isoDaysFromNow,
  isoNow,
  jsonResponse,
  parseJsonBody,
} from "../utils";

export async function handleVerify(
  request: Request,
  env: Env
): Promise<Response> {
  const body = await parseJsonBody<VerifyRequest>(request);
  if (
    !body ||
    typeof body.key !== "string" ||
    typeof body.machine_fingerprint !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "key と machine_fingerprint は必須です",
      400
    );
  }

  // キー形式検証
  const plan = await validateKey(
    body.key,
    env.PRODUCT_PREFIX,
    env.HMAC_SECRET
  );
  if (plan === null) {
    return errorResponse(
      "signature_invalid",
      "ライセンスキーの形式または署名が不正です",
      400
    );
  }

  // blacklist 確認（最優先）
  const blacklist = await env.LICENSES.get(`blacklist:${body.key}`);
  if (blacklist !== null) {
    return errorResponse(
      "key_revoked",
      "このライセンスキーは失効しています",
      403
    );
  }

  // レコード取得
  const recordJson = await env.LICENSES.get(`key:${body.key}`);
  if (!recordJson) {
    return errorResponse("key_not_found", "ライセンスが見つかりません", 404);
  }
  const record: LicenseRecord = JSON.parse(recordJson);

  // 該当マシンが登録されているか確認
  const machine = record.machines.find(
    (m) => m.fingerprint === body.machine_fingerprint
  );
  if (!machine) {
    return errorResponse(
      "fingerprint_mismatch",
      "このマシンはアクティベーションされていません",
      403,
      "再アクティベーションしてください"
    );
  }

  // 期限切れキー
  if (record.expires_at && new Date(record.expires_at) < new Date()) {
    return errorResponse(
      "key_expired",
      "ライセンスキーの有効期限が切れています",
      403
    );
  }

  // 最終確認時刻を更新
  machine.last_verified_at = isoNow();
  await env.LICENSES.put(`key:${body.key}`, JSON.stringify(record));

  // 新しい credential を発行
  const heartbeatDays = parseInt(env.HEARTBEAT_INTERVAL_DAYS, 10);
  const nextVerifyAt = isoDaysFromNow(heartbeatDays);
  const machineSlot =
    record.machines.findIndex(
      (m) => m.fingerprint === body.machine_fingerprint
    ) + 1;
  const credentialPayload = {
    key: body.key,
    plan,
    machine_fingerprint: body.machine_fingerprint,
    support_expires_at: record.support_expires_at,
    extension_expires_at: record.extension_expires_at,
    issued_at: isoNow(),
    next_verify_at: nextVerifyAt,
    machine_slot: machineSlot,
  };
  const credentialRefresh = await generateCredential(
    credentialPayload,
    env.HMAC_SECRET
  );

  const response: VerifyResponse = {
    status: "ok",
    credential_refresh: credentialRefresh,
    next_verify_at: nextVerifyAt,
  };
  return jsonResponse(response);
}
