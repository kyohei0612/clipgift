/**
 * POST /activate — 初回アクティベーション
 *
 * フロー:
 * 1. キー形式 + HMAC 署名検証
 * 2. KV からライセンスレコード取得
 * 3. ステータス確認（revoked なら拒否）
 * 4. マシン台数確認（既登録なら slot 再利用、新規なら追加 / 上限超過なら拒否）
 * 5. credential 生成 + 返却
 */

import type {
  ActivateRequest,
  ActivateResponse,
  Env,
  LicenseRecord,
  MachineRecord,
} from "../types";
import { generateCredential, validateKey } from "../keys";
import {
  errorResponse,
  isoDaysFromNow,
  isoNow,
  jsonResponse,
  parseJsonBody,
} from "../utils";

export async function handleActivate(
  request: Request,
  env: Env
): Promise<Response> {
  const body = await parseJsonBody<ActivateRequest>(request);
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

  // キー形式 + HMAC 署名検証
  const plan = await validateKey(
    body.key,
    env.PRODUCT_PREFIX,
    env.HMAC_SECRET
  );
  if (plan === null) {
    return errorResponse(
      "signature_invalid",
      "ライセンスキーの形式または署名が不正です",
      400,
      "キーをコピペし直してください。それでも解決しない場合はサポートまで"
    );
  }

  // 失効済みかチェック（blacklist 確認）
  const blacklist = await env.LICENSES.get(`blacklist:${body.key}`);
  if (blacklist !== null) {
    return errorResponse(
      "key_revoked",
      "このライセンスキーは失効しています",
      403,
      "返金処理済み or 不正検出による停止です。サポートまで"
    );
  }

  // KV からレコード取得
  const recordJson = await env.LICENSES.get(`key:${body.key}`);
  if (!recordJson) {
    return errorResponse(
      "key_not_found",
      "ライセンスキーが見つかりません",
      404
    );
  }
  const record: LicenseRecord = JSON.parse(recordJson);

  // 期限切れキー（issued から長期間アクティベーションされなかった想定）
  if (record.expires_at && new Date(record.expires_at) < new Date()) {
    return errorResponse(
      "key_expired",
      "ライセンスキーの有効期限が切れています",
      403
    );
  }

  // マシン台数チェック
  const maxMachines = parseInt(env.MAX_MACHINES, 10);
  const existingMachine = record.machines.find(
    (m) => m.fingerprint === body.machine_fingerprint
  );

  let machineSlot: number;
  if (existingMachine) {
    // 既登録マシン → last_verified_at を更新するだけ
    existingMachine.last_verified_at = isoNow();
    machineSlot =
      record.machines.findIndex(
        (m) => m.fingerprint === body.machine_fingerprint
      ) + 1;
  } else {
    // 新規マシン → 上限チェック
    if (record.machines.length >= maxMachines) {
      return errorResponse(
        "max_machines_reached",
        `このライセンスは既に ${maxMachines} 台でアクティベーション済みです`,
        403,
        "別のマシンで使う場合は、既存マシンで「ライセンスを解除」を実行してください"
      );
    }
    const newMachine: MachineRecord = {
      fingerprint: body.machine_fingerprint,
      label: body.machine_label || `machine-${record.machines.length + 1}`,
      activated_at: isoNow(),
      last_verified_at: isoNow(),
    };
    record.machines.push(newMachine);
    machineSlot = record.machines.length;
  }

  // ステータス更新
  if (record.status === "unactivated") {
    record.status = "active";
  }

  // KV 保存
  await env.LICENSES.put(`key:${body.key}`, JSON.stringify(record));
  await env.LICENSES.put(
    `machine:${body.key}:${body.machine_fingerprint}`,
    JSON.stringify({
      label: body.machine_label || "",
      activated_at: existingMachine?.activated_at || isoNow(),
      last_verified_at: isoNow(),
    })
  );

  // credential 生成（アプリ側で署名検証してオフライン使用）
  const heartbeatDays = parseInt(env.HEARTBEAT_INTERVAL_DAYS, 10);
  const nextVerifyAt = isoDaysFromNow(heartbeatDays);
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
  const credential = await generateCredential(
    credentialPayload,
    env.HMAC_SECRET
  );

  const response: ActivateResponse = {
    status: "ok",
    credential,
    plan,
    support_expires_at: record.support_expires_at,
    extension_expires_at: record.extension_expires_at,
    machine_slot: machineSlot,
    next_verify_at: nextVerifyAt,
  };
  return jsonResponse(response);
}
