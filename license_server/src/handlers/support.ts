/**
 * サポートセンター連携エンドポイント:
 *   POST /support/report  — アプリ → エラー報告受付（公開、レート制限あり）
 *   POST /support/notify  — kyohei ローカル → 修正案レビュー依頼通知（Bearer 認証）
 *   POST /support/reply   — kyohei ローカル → ユーザー返信送信（Bearer 認証）
 *
 * すべて内部で Resend API を呼んでメール送信。
 * Resend API キーは Cloudflare Secrets で管理（コードに出さない）。
 *
 * 設計書: .company/engineering/docs/support-center.md
 */

import type {
  Env,
  SupportReportRequest,
  SupportReportResponse,
  SupportNotifyRequest,
  SupportReplyRequest,
  SupportSendResponse,
} from "../types";
import {
  checkAdminAuth,
  errorResponse,
  isoNow,
  jsonResponse,
  parseJsonBody,
} from "../utils";

const RESEND_API_URL = "https://api.resend.com/emails";

/**
 * レート制限: 同一 IP から 60 秒内に 5 件以上は拒否
 * KV キー: `support_rate:{IP}`
 */
const RATE_WINDOW_SECONDS = 60;
const RATE_LIMIT_COUNT = 5;

export async function handleSupportReport(
  request: Request,
  env: Env
): Promise<Response> {
  // Resend 設定が無ければ即 503
  if (!env.RESEND_API_KEY || !env.SUPPORT_FORWARD_TO || !env.SUPPORT_FROM_ADDRESS) {
    return errorResponse(
      "internal_error",
      "サポートセンター未設定です。後ほど再度お試しください。",
      503
    );
  }

  const body = await parseJsonBody<SupportReportRequest>(request);
  if (
    !body ||
    typeof body.app_version !== "string" ||
    typeof body.error_log !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "app_version / error_log は必須です",
      400
    );
  }

  // レート制限
  const clientIp =
    request.headers.get("cf-connecting-ip") ||
    request.headers.get("x-forwarded-for") ||
    "unknown";
  const rateOk = await checkRateLimit(env, clientIp);
  if (!rateOk) {
    return errorResponse(
      "invalid_request",
      "短時間に大量の報告を受信しました。1 分ほど待ってから再送信してください。",
      429
    );
  }

  // 種別判定（既定はエラー報告、後方互換）
  const reportType: "error" | "request" =
    body.report_type === "request" ? "request" : "error";

  // 添付ファイル検証（最大 3 枚 / 各 5MB / image/* のみ）
  const attachments = Array.isArray(body.attachments) ? body.attachments : [];
  if (attachments.length > 3) {
    return errorResponse(
      "invalid_request",
      "添付ファイルは最大 3 枚までです",
      400
    );
  }
  // エラー報告は画像必須、ご要望は任意
  if (reportType === "error" && attachments.length === 0) {
    return errorResponse(
      "invalid_request",
      "エラー報告にはスクリーンショット添付が必要です（最低 1 枚）",
      400
    );
  }
  for (const att of attachments) {
    if (
      !att ||
      typeof att.filename !== "string" ||
      typeof att.content_base64 !== "string" ||
      typeof att.content_type !== "string"
    ) {
      return errorResponse(
        "invalid_request",
        "添付ファイル形式が不正です（filename/content_base64/content_type 必須）",
        400
      );
    }
    if (!att.content_type.startsWith("image/")) {
      return errorResponse(
        "invalid_request",
        "添付できるのは画像ファイル（image/*）のみです",
        400
      );
    }
    // Base64 を decode せずサイズ概算: base64 1 文字 = 0.75 byte
    const approxBytes = Math.floor(att.content_base64.length * 0.75);
    if (approxBytes > 5 * 1024 * 1024) {
      return errorResponse(
        "invalid_request",
        `添付ファイル「${att.filename}」が 5MB を超えています`,
        400
      );
    }
  }

  // メール本文を組み立て（日本語）
  const reportId = crypto.randomUUID();
  const receivedAt = isoNow();
  const subject = buildReportSubject(body, reportType);
  const text = buildReportBody(body, reportId, receivedAt, attachments.length, reportType);

  try {
    if (reportType === "request") {
      // ご要望は 2 通同時送信:
      //   ① nekodori (SUPPORT_FORWARD_TO) → 要望通知（watcher が検知して .company/support/requests/ に保管）
      //   ② clipgift.dev (SUPPORT_FORWARD_TO_REQUEST) → 対応依頼（kyohei が手動返信）
      const ver = body.app_version || "unknown";
      const head = (body.user_comment || "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 30);
      const notifySubject = `【ClipGift 要望通知】v${ver}${head ? " - " + head : ""}`;
      const notifyText =
        "📬 ご要望が届きました。\n" +
        "返信は clipgift.dev@gmail.com 側から行ってください。\n" +
        "（このメールは秘書 watcher が読んで `.company/support/requests/` に保管します）\n" +
        "\n────────────────────────\n" +
        text;
      const handleText =
        "📬 こちらのアカウントよりご要望が届いています。\n" +
        "ユーザー宛の返信はこちらから直接行ってください。\n" +
        "\n────────────────────────\n" +
        text;
      const requestForwardTo =
        env.SUPPORT_FORWARD_TO_REQUEST || env.SUPPORT_FORWARD_TO;

      // 通知メール（nekodori、添付なし - 通知用のため）
      await sendViaResend(env, {
        from: env.SUPPORT_FROM_ADDRESS,
        to: env.SUPPORT_FORWARD_TO,
        subject: notifySubject,
        text: notifyText,
      });
      // 対応依頼メール（clipgift.dev、添付あり）
      await sendViaResend(env, {
        from: env.SUPPORT_FROM_ADDRESS,
        to: requestForwardTo,
        subject,
        text: handleText,
        attachments,
      });
    } else {
      // エラー報告: 既存通り 1 通（添付付き）
      await sendViaResend(env, {
        from: env.SUPPORT_FROM_ADDRESS,
        to: env.SUPPORT_FORWARD_TO,
        subject,
        text,
        attachments,
      });
    }
  } catch (e) {
    console.error("Resend 送信失敗:", e);
    return errorResponse(
      "internal_error",
      "メール送信に失敗しました。サーバー側で再試行されます。",
      503
    );
  }

  // 受信メタを KV に保存（あとで notify / reply で参照する想定）
  try {
    await env.LICENSES.put(
      `support:report:${reportId}`,
      JSON.stringify({
        report_id: reportId,
        received_at: receivedAt,
        user_email: body.user_email || "",
        app_version: body.app_version,
        os: body.os,
        license_tail: body.license_tail,
      }),
      { expirationTtl: 60 * 60 * 24 * 90 } // 90 日
    );
  } catch (e) {
    console.warn("KV 保存失敗（非致命）:", e);
  }

  // v4 アーキ: ローカル watcher 用の pending トリガーを書き込む
  // （Email Worker を通らないアプリ HTTP 経路でも state.json を作れるように）
  // エラー報告 → incoming_error / 要望 → incoming_request
  try {
    const triggerType =
      reportType === "request" ? "incoming_request" : "incoming_error";
    const triggerRecord = {
      id: reportId,
      trigger_type: triggerType,
      subject,
      from: env.SUPPORT_FROM_ADDRESS || "support@clipgift.org",
      body: text.slice(0, 10000),
      in_reply_to: "",
      // app 経路は Resend 送信前なので Message-ID 不明 → report_id を識別子として使う
      message_id: `app-report-${reportId}`,
      received_at: receivedAt,
      report_id: reportId,
      user_email: body.user_email || "",
    };
    const ttl = 60 * 60 * 24 * 7; // 7 日
    await env.LICENSES.put(
      `support:incoming:${reportId}`,
      JSON.stringify(triggerRecord),
      { expirationTtl: ttl }
    );
    await env.LICENSES.put(`support:pending:${reportId}`, "1", {
      expirationTtl: ttl,
    });
    // v5.1: KV 節約のため queue_marker も更新（watcher が cheap GET で検知）。
    // marker キーは TTL なしで永続化（incoming_mail.ts と同じ理由）。
    await env.LICENSES.put("support:queue_marker", receivedAt);
  } catch (e) {
    console.warn("v4 trigger 保存失敗（非致命）:", e);
  }

  const response: SupportReportResponse = {
    status: "ok",
    report_id: reportId,
    received_at: receivedAt,
  };
  return jsonResponse(response);
}

export async function handleSupportNotify(
  request: Request,
  env: Env
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }
  if (!env.RESEND_API_KEY || !env.SUPPORT_FORWARD_TO || !env.SUPPORT_FROM_ADDRESS) {
    return errorResponse("internal_error", "サポートセンター未設定です", 503);
  }

  const body = await parseJsonBody<SupportNotifyRequest>(request);
  if (
    !body ||
    typeof body.error_hash !== "string" ||
    typeof body.repair_summary !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "error_hash / repair_summary は必須です",
      400
    );
  }

  const subject = `【ClipGift 修正案レビュー依頼】${body.error_hash}`;
  const text = [
    "ClipGift エラー報告に対する修正案が完成しました。",
    "下記の内容をご確認の上、このメールに「OK」と返信すれば自動でユーザーに送信されます。",
    "",
    "─────────────────────────",
    "【ユーザー報告（要約）】",
    "─────────────────────────",
    body.original_error_summary || "（要約なし）",
    "",
    body.repair_summary,
    "",
    `error_hash: ${body.error_hash}`,
    `返信先ユーザー: ${body.user_email || "（なし）"}`,
    "",
    "---",
    "このメールは ClipGift サポートセンターから自動送信されています。",
    "本メールに直接返信すると秘書（Claude）が次のアクションを実行します。",
  ].join("\n");

  try {
    const resp = await sendViaResend(env, {
      from: env.SUPPORT_FROM_ADDRESS,
      to: env.SUPPORT_FORWARD_TO,
      subject,
      text,
      // Reply-To を nekodori0612@gmail.com に設定（バウンス防止）
      // INBOX 重複は許容、Gmail フィルターで対応推奨
    });
    const response: SupportSendResponse = { status: "ok", resend_id: resp?.id };
    return jsonResponse(response);
  } catch (e) {
    console.error("notify 送信失敗:", e);
    return errorResponse("internal_error", "通知送信に失敗しました", 503);
  }
}

export async function handleSupportReply(
  request: Request,
  env: Env
): Promise<Response> {
  if (!checkAdminAuth(request, env)) {
    return errorResponse("unauthorized", "認証エラー", 401);
  }
  if (!env.RESEND_API_KEY || !env.SUPPORT_FROM_ADDRESS) {
    return errorResponse("internal_error", "サポートセンター未設定です", 503);
  }

  const body = await parseJsonBody<SupportReplyRequest>(request);
  if (
    !body ||
    typeof body.to !== "string" ||
    typeof body.subject !== "string" ||
    typeof body.body !== "string"
  ) {
    return errorResponse(
      "invalid_request",
      "to / subject / body は必須です",
      400
    );
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.to)) {
    return errorResponse(
      "invalid_request",
      "to のメールアドレス形式が不正です",
      400
    );
  }

  try {
    const resp = await sendViaResend(env, {
      from: env.SUPPORT_FROM_ADDRESS,
      to: body.to,
      subject: body.subject,
      text: body.body,
    });
    const response: SupportSendResponse = { status: "ok", resend_id: resp?.id };
    return jsonResponse(response);
  } catch (e) {
    const errMsg = e instanceof Error ? e.message : String(e);
    console.error(`reply 送信失敗 to=${body.to}:`, errMsg);
    // Resend のエラーメッセージをそのまま返してデバッグしやすくする
    return errorResponse(
      "internal_error",
      `ユーザー返信送信に失敗しました: ${errMsg.slice(0, 200)}`,
      503
    );
  }
}

// ────────────────────────────────────────────────────────────
// 内部ヘルパー
// ────────────────────────────────────────────────────────────

interface ResendSendArgs {
  from: string;
  to: string;
  subject: string;
  text: string;
  /** Reply-To アドレス（ユーザー視点で「返信」押すと届く先）。未指定なら from と同じ。 */
  replyTo?: string;
  /** true なら Reply-To を一切設定しない（kyohei → kyohei の確認依頼で INBOX 重複を防ぐ） */
  skipReplyTo?: boolean;
  /** 添付ファイル（Resend 形式に内部で変換） */
  attachments?: { filename: string; content_base64: string; content_type: string }[];
}

interface ResendResponse {
  id?: string;
}

async function sendViaResend(
  env: Env,
  args: ResendSendArgs
): Promise<ResendResponse | null> {
  // Reply-To: 引数優先 → 環境変数 SUPPORT_REPLY_TO → フォワード先 SUPPORT_FORWARD_TO
  // skipReplyTo=true の場合は完全に設定しない（kyohei → kyohei の自己宛は INBOX 重複防止のため）
  let replyTo: string | undefined;
  if (!args.skipReplyTo) {
    replyTo =
      args.replyTo ||
      env.SUPPORT_REPLY_TO ||
      env.SUPPORT_FORWARD_TO ||
      undefined;
  }

  const payload: Record<string, unknown> = {
    from: args.from,
    to: [args.to],
    subject: args.subject,
    text: args.text,
  };
  if (replyTo) {
    payload.reply_to = replyTo;
  }
  if (args.attachments && args.attachments.length > 0) {
    // Resend API 仕様: { filename, content (Base64 文字列) }
    payload.attachments = args.attachments.map((a) => ({
      filename: a.filename,
      content: a.content_base64,
      content_type: a.content_type,
    }));
  }

  const resp = await fetch(RESEND_API_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.RESEND_API_KEY}`,
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`Resend ${resp.status}: ${errText.slice(0, 300)}`);
  }
  try {
    return (await resp.json()) as ResendResponse;
  } catch {
    return null;
  }
}

async function checkRateLimit(env: Env, ip: string): Promise<boolean> {
  if (ip === "unknown") {
    return true; // 識別不能なら通過させる（過剰ブロック回避）
  }
  const key = `support_rate:${ip}`;
  const current = (await env.LICENSES.get(key)) || "0";
  const count = parseInt(current, 10) || 0;
  if (count >= RATE_LIMIT_COUNT) {
    return false;
  }
  await env.LICENSES.put(key, String(count + 1), {
    expirationTtl: RATE_WINDOW_SECONDS,
  });
  return true;
}

function buildReportSubject(
  body: SupportReportRequest,
  reportType: "error" | "request"
): string {
  const ver = body.app_version || "unknown";
  if (reportType === "request") {
    // ご要望はユーザーコメントの冒頭を件名に取る
    const head = (body.user_comment || "").replace(/\s+/g, " ").trim().slice(0, 30);
    return `【ClipGift ご要望】v${ver}${head ? " - " + head : ""}`;
  }
  const errorType = extractErrorType(body.error_log);
  return `【ClipGift エラー報告】v${ver}${errorType ? " - " + errorType : ""}`;
}

function extractErrorType(log: string): string {
  // ログ最後尾から `XxxError: ...` のパターンを探す
  const match = log.match(/([A-Za-z][A-Za-z0-9]*Error|Exception)[^\n]{0,80}/);
  return match ? match[0].slice(0, 80) : "";
}

function buildReportBody(
  body: SupportReportRequest,
  reportId: string,
  receivedAt: string,
  attachmentCount: number = 0,
  reportType: "error" | "request" = "error"
): string {
  const typeLabel = reportType === "request" ? "ご要望" : "エラー報告";
  const headerLine = reportType === "request"
    ? "ClipGift からご要望が届きました。"
    : "ClipGift からエラー報告を受信しました。";
  const replyHint = body.user_email
    ? `ユーザー返信先: ${body.user_email}`
    : "ユーザー返信先: （なし、返信不要）";

  const envInfo = body.env_info
    ? Object.entries(body.env_info)
        .map(([k, v]) => `- ${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
        .join("\n")
    : "（なし）";

  return [
    headerLine,
    `（種別: ${typeLabel}）`,
    "",
    "【アプリ情報】",
    `- バージョン: ${body.app_version}`,
    `- OS: ${body.os || "不明"}`,
    `- ライセンス末尾: ${body.license_tail ? "****-" + body.license_tail : "なし"}`,
    `- ${replyHint}`,
    `- スクリーンショット: ${attachmentCount} 枚 添付`,
    "",
    "【ユーザーコメント】",
    body.user_comment || "（記述なし）",
    "",
    "【自動収集ログ（マスキング済 / 原文）】",
    body.error_log || "（ログなし）",
    "",
    "【環境情報】",
    envInfo,
    "",
    "【受信メタ】",
    `- report_id: ${reportId}`,
    `- received_at: ${receivedAt}`,
    "",
    "---",
    "このメールは ClipGift サポートセンターから自動送信されています。",
    "返信は不要です（双方向通信は別経路）。",
  ].join("\n");
}
