/**
 * ライセンスキー生成・署名・検証
 *
 * キー形式: CGFT-{PLAN}-XXXX-XXXX-XXXX
 *   XXXX 部分は base32 風（混同しやすい I/O/0/1 を除外した文字セット）
 *   末尾 4 文字は HMAC 署名の先頭 4 文字（オフライン部分検証用）
 */

import type { Plan } from "./types";

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 32 文字、I/O/0/1 除外

function planCode(plan: Plan): string {
  switch (plan) {
    case "lite":
      return "LITE";
    case "std":
      return "STD";
    case "ext":
      return "EXT";
  }
}

function planFromCode(code: string): Plan | null {
  switch (code) {
    case "LITE":
      return "lite";
    case "STD":
      return "std";
    case "EXT":
      return "ext";
    default:
      return null;
  }
}

/**
 * Crypto.getRandomValues で 4 文字グループ × 2 を生成（合計 8 文字）
 * 末尾 4 文字は HMAC 署名のため後で埋める
 */
function generateRandomGroups(): string {
  const buf = new Uint8Array(8);
  crypto.getRandomValues(buf);
  let result = "";
  for (let i = 0; i < 8; i++) {
    result += ALPHABET[buf[i] % ALPHABET.length];
  }
  return `${result.slice(0, 4)}-${result.slice(4, 8)}`;
}

/**
 * HMAC-SHA256 署名 → base32 風文字列の先頭 4 文字
 */
async function shortHmac(secret: string, message: string): Promise<string> {
  const keyData = new TextEncoder().encode(secret);
  const msgData = new TextEncoder().encode(message);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, msgData);
  const bytes = new Uint8Array(sig);
  let result = "";
  for (let i = 0; i < 4; i++) {
    result += ALPHABET[bytes[i] % ALPHABET.length];
  }
  return result;
}

/**
 * 完全な HMAC-SHA256 を base64url で返す（credential 用）
 */
export async function fullHmac(
  secret: string,
  message: string
): Promise<string> {
  const keyData = new TextEncoder().encode(secret);
  const msgData = new TextEncoder().encode(message);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, msgData);
  return arrayBufferToBase64Url(sig);
}

function arrayBufferToBase64Url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * 新規ライセンスキーを生成
 *
 * @param plan プラン種別
 * @param prefix プロダクト固定 prefix（環境変数から）
 * @param hmacSecret HMAC 署名用シークレット
 * @returns CGFT-{PLAN}-XXXX-XXXX-XXXX 形式のキー
 */
export async function generateLicenseKey(
  plan: Plan,
  prefix: string,
  hmacSecret: string
): Promise<string> {
  const planStr = planCode(plan);
  const groups = generateRandomGroups();
  const prefixPart = `${prefix}-${planStr}-${groups}`;
  const sig = await shortHmac(hmacSecret, prefixPart);
  return `${prefixPart}-${sig}`;
}

/**
 * キー形式の妥当性を検証（HMAC 署名検証含む）
 *
 * @returns プラン種別 or null（不正キー）
 */
export async function validateKey(
  key: string,
  prefix: string,
  hmacSecret: string
): Promise<Plan | null> {
  const parts = key.split("-");
  if (parts.length !== 5) {
    return null;
  }
  const [pfx, planStr, g1, g2, sig] = parts;
  if (pfx !== prefix) {
    return null;
  }
  if (g1.length !== 4 || g2.length !== 4 || sig.length !== 4) {
    return null;
  }
  const plan = planFromCode(planStr);
  if (plan === null) {
    return null;
  }
  // 全文字が ALPHABET に含まれているか確認
  for (const ch of `${g1}${g2}${sig}`) {
    if (!ALPHABET.includes(ch)) {
      return null;
    }
  }
  // 署名検証
  const expectedSig = await shortHmac(
    hmacSecret,
    `${pfx}-${planStr}-${g1}-${g2}`
  );
  if (sig !== expectedSig) {
    return null;
  }
  return plan;
}

/**
 * credential（アプリ側に渡す署名済みデータ）を生成
 *
 * フォーマット: base64url(payload).base64url(signature)
 */
export async function generateCredential(
  payload: object,
  hmacSecret: string
): Promise<string> {
  const json = JSON.stringify(payload);
  const payloadB64 = btoa(unescape(encodeURIComponent(json)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const sig = await fullHmac(hmacSecret, payloadB64);
  return `${payloadB64}.${sig}`;
}
