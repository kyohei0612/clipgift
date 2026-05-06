/**
 * POST /deactivate — マシン解放
 *
 * ユーザーが「このマシンから解除する」を実行 or アンインストール時に呼ぶ。
 * 当該 fingerprint の machine を slot から削除し、空きを 1 つ作る。
 */

import type {
  DeactivateRequest,
  DeactivateResponse,
  Env,
  LicenseRecord,
} from "../types";
import { validateKey } from "../keys";
import {
  errorResponse,
  jsonResponse,
  parseJsonBody,
} from "../utils";

export async function handleDeactivate(
  request: Request,
  env: Env
): Promise<Response> {
  const body = await parseJsonBody<DeactivateRequest>(request);
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

  // キー形式検証（不正キーで KV を圧迫しないよう）
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

  const recordJson = await env.LICENSES.get(`key:${body.key}`);
  if (!recordJson) {
    return errorResponse("key_not_found", "ライセンスが見つかりません", 404);
  }
  const record: LicenseRecord = JSON.parse(recordJson);

  // 該当マシンを削除
  const before = record.machines.length;
  record.machines = record.machines.filter(
    (m) => m.fingerprint !== body.machine_fingerprint
  );

  // 削除した分だけマシンレコードも消す
  if (record.machines.length !== before) {
    await env.LICENSES.delete(
      `machine:${body.key}:${body.machine_fingerprint}`
    );
  }

  // 全マシン解放されたら status を unactivated に戻す
  if (record.machines.length === 0) {
    record.status = "unactivated";
  }

  await env.LICENSES.put(`key:${body.key}`, JSON.stringify(record));

  const maxMachines = parseInt(env.MAX_MACHINES, 10);
  const response: DeactivateResponse = {
    status: "ok",
    remaining_slots: maxMachines - record.machines.length,
  };
  return jsonResponse(response);
}
