/**
 * POST /stripe/webhook — Phase 2 サブスク土台（DORMANT / 眠ったまま）
 *
 * ★ このハンドラは env.STRIPE_WEBHOOK_SECRET が未設定なら 503 を返し、
 *   何もしない。Phase 1（買い切り）運用には一切影響しない。
 *   Phase 2 で Stripe を繋ぐときに secret を設定すると起動する。
 *
 * 設計思想:
 *   サブスク = 「Stripe 入金 webhook が expires_at を毎課金で先送りする買い切り」。
 *   既存の /activate /verify /machine_id 機構をそのまま再利用する。
 *   ・購入時にライセンスキーを自動発行 → Resend で購入者メールへ送付
 *   ・入金 (invoice.paid) のたびに expires_at を現課金期間末へ前進
 *   ・解約/未払いは subscription_status を更新、期間末で自然に key_expired
 *
 * 対応イベント:
 *   checkout.session.completed      … キー発行 + メール送付（作成の起点）
 *   invoice.paid / payment_succeeded … expires_at を期間末へ前進、active 化
 *   invoice.payment_failed          … past_due 化（Stripe 再試行に委ねる）
 *   customer.subscription.updated   … 状態 + 期間末を同期
 *   customer.subscription.deleted   … canceled 化（期間末まではアクセス維持）
 *
 * KV マッピング（追加分）:
 *   stripe_sub:{subscriptionId}      -> { key }
 *   stripe_customer:{customerId}     -> { key }
 */

import type { Env, LicenseRecord, SubscriptionStatus } from "../types";
import { generateLicenseKey } from "../keys";
import {
  billingTypeOf,
  errorResponse,
  isoDaysFromNow,
  isoFromUnixSeconds,
  isoNow,
  jsonResponse,
} from "../utils";

const RESEND_API_URL = "https://api.resend.com/emails";
// 署名タイムスタンプの許容ズレ（リプレイ攻撃対策）
const SIGNATURE_TOLERANCE_SECONDS = 5 * 60;
// 初回発行時の暫定期限（invoice.paid が来るまでのつなぎ）。初月 + 猶予。
const INITIAL_PERIOD_DAYS = 35;

export async function handleStripeWebhook(
  request: Request,
  env: Env
): Promise<Response> {
  // ── DORMANT ガード: Stripe 未設定なら何もしない ──
  if (!env.STRIPE_WEBHOOK_SECRET) {
    return errorResponse(
      "invalid_request",
      "サブスク機能は未設定です（Phase 2 で有効化）",
      503
    );
  }

  // 署名検証には「生ボディ」が必要（JSON.parse 前の文字列）
  const raw = await request.text();
  const sigHeader = request.headers.get("stripe-signature");
  const signatureOk = await verifyStripeSignature(
    raw,
    sigHeader,
    env.STRIPE_WEBHOOK_SECRET
  );
  if (!signatureOk) {
    return errorResponse("signature_invalid", "Stripe 署名検証に失敗しました", 400);
  }

  let event: StripeEvent;
  try {
    event = JSON.parse(raw) as StripeEvent;
  } catch {
    return errorResponse("invalid_request", "ボディの JSON パースに失敗しました", 400);
  }

  const obj = (event.data?.object ?? {}) as Record<string, unknown>;

  try {
    switch (event.type) {
      case "checkout.session.completed":
        await handleCheckoutCompleted(env, obj);
        break;
      case "invoice.paid":
      case "invoice.payment_succeeded":
        await handleInvoicePaid(env, obj);
        break;
      case "invoice.payment_failed":
        await handleInvoiceFailed(env, obj);
        break;
      case "customer.subscription.updated":
        await handleSubscriptionUpdated(env, obj);
        break;
      case "customer.subscription.deleted":
        await handleSubscriptionDeleted(env, obj);
        break;
      default:
        // 未対応イベントは 200 で握りつぶす（Stripe に無駄な再送をさせない）
        break;
    }
  } catch (e) {
    console.error(`stripe webhook 処理エラー (${event.type}):`, e);
    // 500 を返すと Stripe が再送してくれる（一時障害からの自動回復）
    return errorResponse("internal_error", "webhook 処理中にエラー", 500);
  }

  return jsonResponse({ status: "ok", type: event.type });
}

// ───────────────────────────── イベント別処理 ─────────────────────────────

/**
 * checkout.session.completed（mode=subscription）:
 * ライセンスキーを自動発行し、購入者メールへ送付する（作成の起点）。
 * 冪等: 同一 subscription に対して二重発行しない。
 */
async function handleCheckoutCompleted(
  env: Env,
  session: Record<string, unknown>
): Promise<void> {
  if (session.mode !== "subscription") {
    return; // 買い切り Checkout 等は対象外
  }
  const subscriptionId = asString(session.subscription);
  const customerId = asString(session.customer);
  const email = extractCheckoutEmail(session);
  if (!subscriptionId || !email) {
    console.warn("checkout.session.completed: subscription/email 不足", {
      subscriptionId,
      hasEmail: Boolean(email),
    });
    return;
  }

  // 冪等ガード: 既に発行済みなら何もしない
  const existing = await findKeyBySubscription(env, subscriptionId);
  if (existing) {
    return;
  }

  const key = await generateLicenseKey(
    "single",
    env.PRODUCT_PREFIX,
    env.HMAC_SECRET
  );
  // invoice.paid が正確な期間末で上書きするまでの暫定期限
  const initialExpiry = isoDaysFromNow(INITIAL_PERIOD_DAYS);

  const record: LicenseRecord = {
    key,
    plan: "single",
    issued_at: isoNow(),
    expires_at: initialExpiry,
    status: "unactivated",
    machines: [],
    order_source: "stripe_subscription",
    order_id: subscriptionId,
    buyer_email: email,
    support_expires_at: initialExpiry,
    extension_expires_at: initialExpiry,
    notes: `Stripe subscription ${subscriptionId} / customer ${customerId ?? "?"}`,
    billing_type: "subscription",
    subscription_status: "active",
    stripe_customer_id: customerId ?? undefined,
    stripe_subscription_id: subscriptionId,
    current_period_end: initialExpiry,
  };

  await env.LICENSES.put(`key:${key}`, JSON.stringify(record));
  await env.LICENSES.put(
    `stripe_sub:${subscriptionId}`,
    JSON.stringify({ key })
  );
  if (customerId) {
    await env.LICENSES.put(
      `stripe_customer:${customerId}`,
      JSON.stringify({ key })
    );
  }
  await trackEmailKey(env, email, key);

  await sendKeyEmail(env, email, key);
  console.log(`サブスクキー発行: sub=${subscriptionId} key=${maskKey(key)}`);
}

/**
 * invoice.paid / invoice.payment_succeeded:
 * expires_at を現課金期間末へ前進させ、active 化する。
 */
async function handleInvoicePaid(
  env: Env,
  invoice: Record<string, unknown>
): Promise<void> {
  const subscriptionId = extractInvoiceSubscriptionId(invoice);
  if (!subscriptionId) {
    return; // サブスク以外の請求は対象外
  }
  const record = await loadRecordBySubscription(env, subscriptionId);
  if (!record) {
    console.warn(`invoice.paid: 対応キー無し sub=${subscriptionId}`);
    return;
  }
  const periodEnd =
    isoFromUnixSeconds(extractInvoicePeriodEnd(invoice)) ??
    isoFromUnixSeconds(invoice.period_end);
  if (periodEnd) {
    syncPeriod(record, periodEnd);
  }
  record.subscription_status = "active";
  await env.LICENSES.put(`key:${record.key}`, JSON.stringify(record));
}

/**
 * invoice.payment_failed:
 * past_due 化のみ（expires_at は動かさない = Stripe の再試行/猶予に委ねる）。
 */
async function handleInvoiceFailed(
  env: Env,
  invoice: Record<string, unknown>
): Promise<void> {
  const subscriptionId = extractInvoiceSubscriptionId(invoice);
  if (!subscriptionId) {
    return;
  }
  const record = await loadRecordBySubscription(env, subscriptionId);
  if (!record) {
    return;
  }
  record.subscription_status = "past_due";
  await env.LICENSES.put(`key:${record.key}`, JSON.stringify(record));
}

/**
 * customer.subscription.updated:
 * Stripe 側の状態と期間末を同期する。
 */
async function handleSubscriptionUpdated(
  env: Env,
  sub: Record<string, unknown>
): Promise<void> {
  const subscriptionId = asString(sub.id);
  if (!subscriptionId) {
    return;
  }
  const record = await loadRecordBySubscription(env, subscriptionId);
  if (!record) {
    return;
  }
  record.subscription_status = mapStripeStatus(asString(sub.status));
  const periodEnd = isoFromUnixSeconds(sub.current_period_end);
  if (periodEnd) {
    syncPeriod(record, periodEnd);
  }
  await env.LICENSES.put(`key:${record.key}`, JSON.stringify(record));
}

/**
 * customer.subscription.deleted:
 * canceled 化。expires_at は現期間末のまま（払い済み期間はアクセス維持）。
 * ブラックリストには入れない（自然失効に任せる）。
 */
async function handleSubscriptionDeleted(
  env: Env,
  sub: Record<string, unknown>
): Promise<void> {
  const subscriptionId = asString(sub.id);
  if (!subscriptionId) {
    return;
  }
  const record = await loadRecordBySubscription(env, subscriptionId);
  if (!record) {
    return;
  }
  record.subscription_status = "canceled";
  const periodEnd = isoFromUnixSeconds(sub.current_period_end);
  if (periodEnd) {
    // 期間末で失効するよう合わせる（延長はしない）
    syncPeriod(record, periodEnd);
  }
  await env.LICENSES.put(`key:${record.key}`, JSON.stringify(record));
}

// ───────────────────────────── ヘルパー ─────────────────────────────

interface StripeEvent {
  type: string;
  data?: { object?: unknown };
}

/** subscription ID からライセンスキーを引く（存在しなければ null）。 */
async function findKeyBySubscription(
  env: Env,
  subscriptionId: string
): Promise<string | null> {
  const mapped = await env.LICENSES.get(`stripe_sub:${subscriptionId}`);
  if (!mapped) {
    return null;
  }
  try {
    return (JSON.parse(mapped) as { key: string }).key ?? null;
  } catch {
    return null;
  }
}

/** subscription ID からライセンスレコード本体を読む。 */
async function loadRecordBySubscription(
  env: Env,
  subscriptionId: string
): Promise<LicenseRecord | null> {
  const key = await findKeyBySubscription(env, subscriptionId);
  if (!key) {
    return null;
  }
  const json = await env.LICENSES.get(`key:${key}`);
  if (!json) {
    return null;
  }
  try {
    const record = JSON.parse(json) as LicenseRecord;
    // 念のため: サブスク由来レコードのみ扱う（買い切りを誤って触らない）
    if (billingTypeOf(record) !== "subscription") {
      console.warn(`sub=${subscriptionId} が非サブスクレコードを指しています`);
      return null;
    }
    return record;
  } catch {
    return null;
  }
}

/**
 * 課金期間末を各期限フィールドへ反映する。
 * サブスクではアプリ利用可否(expires_at)・サポート・拡張をすべて期間末に揃える。
 */
function syncPeriod(record: LicenseRecord, periodEndIso: string): void {
  record.current_period_end = periodEndIso;
  record.expires_at = periodEndIso;
  record.support_expires_at = periodEndIso;
  record.extension_expires_at = periodEndIso;
}

/** Stripe のサブスク status を内部表現へ写像。 */
function mapStripeStatus(status: string | null): SubscriptionStatus {
  switch (status) {
    case "active":
    case "trialing":
      return "active";
    case "past_due":
    case "unpaid":
    case "incomplete":
      return "past_due";
    case "canceled":
    case "incomplete_expired":
      return "canceled";
    default:
      return "past_due";
  }
}

/** 同一メールのキー一覧を追跡（不正検知用、admin.ts と同じ email:{hash} スキーマ）。 */
async function trackEmailKey(
  env: Env,
  email: string,
  key: string
): Promise<void> {
  try {
    const emailHash = await sha256Hex(email.toLowerCase());
    const existing = await env.LICENSES.get(`email:${emailHash}`);
    const list = existing ? (JSON.parse(existing) as string[]) : [];
    list.push(key);
    await env.LICENSES.put(`email:${emailHash}`, JSON.stringify(list));
  } catch (e) {
    console.warn("email トラッキング保存失敗（非致命）:", e);
  }
}

/** 購入者メールへライセンスキーを送付（Resend）。失敗しても throw しない。 */
async function sendKeyEmail(
  env: Env,
  toEmail: string,
  key: string
): Promise<void> {
  if (!env.RESEND_API_KEY || !env.SUPPORT_FROM_ADDRESS) {
    console.warn("Resend 未設定のためキー送付メールをスキップ");
    return;
  }
  const subject = "【ClipGift】ライセンスキーのお届け";
  const text = [
    "この度は ClipGift をご購読いただきありがとうございます。",
    "",
    "以下があなたのライセンスキーです。ClipGift の認証画面に貼り付けてください。",
    "",
    `    ${key}`,
    "",
    "【初回認証の手順】",
    "1. ClipGift を起動します",
    "2. ライセンスキーの入力欄に上記キーを貼り付けます",
    "3. 認証が完了すればすぐにご利用いただけます",
    "",
    "以降は毎月のお支払いが続く限り、自動で利用が継続されます（キーの再入力は不要です）。",
    "",
    "ご不明な点があればこのメールにご返信ください。",
    "今後とも ClipGift をよろしくお願いいたします。",
    "",
    "---",
    "ClipGift サポート",
  ].join("\n");

  const payload: Record<string, unknown> = {
    from: env.SUPPORT_FROM_ADDRESS,
    to: [toEmail],
    subject,
    text,
  };
  const replyTo = env.SUPPORT_REPLY_TO || env.SUPPORT_FORWARD_TO;
  if (replyTo) {
    payload.reply_to = replyTo;
  }

  try {
    const resp = await fetch(RESEND_API_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.RESEND_API_KEY}`,
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      console.error(
        `キー送付メール送信失敗 HTTP ${resp.status}: ${(await resp.text()).slice(0, 200)}`
      );
    }
  } catch (e) {
    console.error("キー送付メール送信エラー:", e);
  }
}

// ── Stripe 署名検証（Web Crypto、外部ライブラリ不要）──

async function verifyStripeSignature(
  payload: string,
  header: string | null,
  secret: string
): Promise<boolean> {
  if (!header) {
    return false;
  }
  let timestamp = "";
  const v1List: string[] = [];
  for (const part of header.split(",")) {
    const idx = part.indexOf("=");
    if (idx === -1) continue;
    const k = part.slice(0, idx).trim();
    const v = part.slice(idx + 1).trim();
    if (k === "t") timestamp = v;
    else if (k === "v1" && v) v1List.push(v);
  }
  if (!timestamp || v1List.length === 0) {
    return false;
  }
  const ts = parseInt(timestamp, 10);
  if (!Number.isFinite(ts)) {
    return false;
  }
  const nowSec = Math.floor(Date.now() / 1000);
  if (Math.abs(nowSec - ts) > SIGNATURE_TOLERANCE_SECONDS) {
    return false; // リプレイ防止
  }
  const expected = await hmacHex(secret, `${timestamp}.${payload}`);
  return v1List.some((v1) => constantTimeEq(v1, expected));
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(message)
  );
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
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

// ── 小物 ──

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Checkout Session から購入者メールを取り出す。 */
function extractCheckoutEmail(session: Record<string, unknown>): string | null {
  const details = session.customer_details as
    | { email?: unknown }
    | undefined;
  return asString(details?.email) ?? asString(session.customer_email);
}

/**
 * invoice から subscription ID を取り出す。
 * Stripe の API バージョンにより格納位置が異なるため複数経路を試す。
 *   - invoice.subscription（従来）
 *   - invoice.parent.subscription_details.subscription（新しめ）
 *   - invoice.lines.data[0].subscription
 */
function extractInvoiceSubscriptionId(
  invoice: Record<string, unknown>
): string | null {
  const direct = asString(invoice.subscription);
  if (direct) return direct;

  const parent = invoice.parent as
    | { subscription_details?: { subscription?: unknown } }
    | undefined;
  const fromParent = asString(parent?.subscription_details?.subscription);
  if (fromParent) return fromParent;

  const lines = invoice.lines as { data?: unknown } | undefined;
  const data = lines?.data;
  if (Array.isArray(data) && data.length > 0) {
    const fromLine = asString(
      (data[0] as { subscription?: unknown }).subscription
    );
    if (fromLine) return fromLine;
  }
  return null;
}

/** invoice.lines.data[0].period.end を安全に取り出す。 */
function extractInvoicePeriodEnd(invoice: Record<string, unknown>): unknown {
  const lines = invoice.lines as { data?: unknown } | undefined;
  const data = lines?.data;
  if (Array.isArray(data) && data.length > 0) {
    const period = (data[0] as { period?: { end?: unknown } }).period;
    if (period && "end" in period) {
      return period.end;
    }
  }
  return undefined;
}

function maskKey(key: string): string {
  return key.length > 8 ? `${key.slice(0, 8)}…` : key;
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
