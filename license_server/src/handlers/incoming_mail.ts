/**
 * Cloudflare Email Worker: 受信メール処理ハンドラ
 *
 * 役割:
 *   - support@clipgift.org に届いたメールを Cloudflare 上で直接受信
 *   - 件名で種別判定（エラー報告 / ご要望 / kyohei 返信）
 *   - KV に「未処理トリガー」と「メール本文」を保存
 *   - SUPPORT_FORWARD_TO に転送（kyohei が Gmail で見られるように）
 *
 * ローカル watcher は GET /support/pending を 5 秒ごとに poll して新トリガーを取得。
 *
 * 設計書: .company/engineering/docs/support-center.md (v4)
 */
import PostalMime from "postal-mime";
import type { Env } from "../types";

/**
 * トリガー種別（v5: Claude セッション継続方式）
 *   incoming_error      : ユーザーからのエラー報告（local watcher が自動 Claude 起動）
 *   incoming_request    : ユーザーからのご要望（保管、Phase 2 で Claude 判定追加予定）
 *   reply_to_secretary  : kyohei が確認依頼/エラー報告メールに返信 → local watcher が Claude セッション resume → 文意判定
 *   ignore              : 自社送信のループ（確認依頼が INBOX に入る等）
 */
export type IncomingTriggerType =
  | "incoming_error"
  | "incoming_request"
  | "reply_to_secretary"
  | "ignore";

export interface IncomingTriggerRecord {
  id: string;
  trigger_type: IncomingTriggerType;
  subject: string;
  from: string;
  body: string; // 先頭 10000 文字まで保持（解析用）
  in_reply_to: string;
  message_id: string;
  received_at: string;
  /** 元のメールから抽出された error_hash（Re: 修正案レビュー依頼 <hash> 形式の場合） */
  error_hash?: string;
}

const SUBJECT_FILTER_ERROR = "【ClipGift エラー報告】";
const SUBJECT_FILTER_REQUEST = "【ClipGift ご要望】";
const SUBJECT_REVIEW = "【ClipGift 修正案レビュー依頼】";
const SUBJECT_CONFIRM = "【ClipGift 確認依頼】";
const SUBJECT_REQUEST_NOTIFY = "【ClipGift 要望通知】";

/**
 * v5: 件名だけで判定し、本文の文意判定は local watcher の Claude resume に委譲。
 * Email Worker はキーワード判定をしない（誤判定リスクを排除）。
 */
function classifyMessage(args: {
  subject: string;
  body: string;
  from: string;
}): { triggerType: IncomingTriggerType; errorHash?: string } {
  const subject = args.subject.trim();

  // 自社送信のループ（Email Routing で BCC 等が戻ってくる、kyohei 自身宛に Reply-To 経由で来る等）
  // Re: で始まらない自社系件名 = 自分が送ったやつ → 無視
  if (
    subject.includes(SUBJECT_REVIEW) ||
    subject.includes(SUBJECT_CONFIRM) ||
    subject.includes(SUBJECT_REQUEST_NOTIFY)
  ) {
    if (!/^\s*Re\s*:/i.test(subject)) {
      return { triggerType: "ignore" };
    }
  }

  const isReply = /^\s*Re\s*:/i.test(subject);

  if (isReply) {
    // 確認依頼/レビュー依頼への返信 → kyohei 返信、Claude セッションで文意判定
    const reviewMatch = subject.match(
      /【ClipGift 修正案レビュー依頼】\s*([0-9a-f]{12})/
    );
    const confirmMatch = subject.match(
      /【ClipGift 確認依頼】\s*([0-9a-f]{12})/
    );
    const reviewHash = reviewMatch?.[1] ?? confirmMatch?.[1];
    if (reviewHash) {
      return { triggerType: "reply_to_secretary", errorHash: reviewHash };
    }

    // エラー報告 / ご要望への返信 → やはり kyohei 返信扱い（hash は local watcher が件名/in_reply_to で同定）
    if (
      subject.includes(SUBJECT_FILTER_ERROR) ||
      subject.includes(SUBJECT_FILTER_REQUEST)
    ) {
      return { triggerType: "reply_to_secretary" };
    }

    return { triggerType: "ignore" };
  }

  // 新規メール（Re: でない）
  if (subject.startsWith(SUBJECT_FILTER_ERROR)) {
    return { triggerType: "incoming_error" };
  }
  if (subject.startsWith(SUBJECT_FILTER_REQUEST)) {
    return { triggerType: "incoming_request" };
  }

  return { triggerType: "ignore" };
}

/**
 * Email Worker ハンドラ。
 * Cloudflare Email Routing で「ルートを Worker に送る」設定にすると、
 * 受信のたびにこの関数が呼ばれる。
 */
export async function handleIncomingMail(
  message: ForwardableEmailMessage,
  env: Env,
  ctx: ExecutionContext
): Promise<void> {
  let parsed: Awaited<ReturnType<typeof PostalMime.prototype.parse>>;
  try {
    parsed = await PostalMime.parse(message.raw as ReadableStream);
  } catch (e) {
    console.error("Email parse error:", e);
    // パース失敗 → そのまま Gmail に転送して終了（運用優先）
    if (env.SUPPORT_FORWARD_TO) {
      try {
        await message.forward(env.SUPPORT_FORWARD_TO);
      } catch (fwdErr) {
        console.error("Forward failed:", fwdErr);
      }
    }
    return;
  }

  const subject = parsed.subject || "";
  const fromAddr =
    (parsed.from && parsed.from.address) || message.from || "unknown";
  const body = parsed.text || parsed.html || "";
  const inReplyTo = parsed.inReplyTo || "";
  const messageId = parsed.messageId || "";

  const { triggerType, errorHash } = classifyMessage({
    subject,
    body,
    from: fromAddr,
  });

  // KV に保存
  const id = crypto.randomUUID();
  const record: IncomingTriggerRecord = {
    id,
    trigger_type: triggerType,
    subject,
    from: fromAddr,
    body: body.slice(0, 10000),
    in_reply_to: inReplyTo.replace(/[<>]/g, "").trim(),
    message_id: messageId.replace(/[<>]/g, "").trim(),
    received_at: new Date().toISOString(),
    error_hash: errorHash,
  };

  // 7 日間保持
  const ttl = 60 * 60 * 24 * 7;

  try {
    await env.LICENSES.put(`support:incoming:${id}`, JSON.stringify(record), {
      expirationTtl: ttl,
    });

    // ignore でないものは pending リストに追加 + marker 更新
    if (triggerType !== "ignore") {
      await env.LICENSES.put(`support:pending:${id}`, "1", {
        expirationTtl: ttl,
      });
      // v5.1: 受信があったことを marker で通知（watcher の cheap GET で検知させる）。
      // marker キーは TTL なしで永続化する。受信のたび上書きされ、消える必要がない。
      // TTL を付けると 7 日受信なしで消える → marker="" に戻り cheap path 失効 → LIST 連発で quota 枯渇。
      await env.LICENSES.put("support:queue_marker", record.received_at);
    }
  } catch (e) {
    console.error("KV write failed:", e);
  }

  // Gmail への転送（人間の目で見る用）
  if (env.SUPPORT_FORWARD_TO) {
    try {
      await message.forward(env.SUPPORT_FORWARD_TO);
    } catch (e) {
      console.error("Forward failed:", e);
    }
  }
}
