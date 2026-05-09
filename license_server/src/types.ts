/**
 * クリップギフト ライセンスサーバー 型定義
 */

/**
 * Phase 1（買い切り）以降のプラン。1 プランのみ運用。
 * 旧 lite / std / ext は後方互換のため keys.ts では decode 可能だが、
 * すべて "single" として正規化される。
 */
export type Plan = "single";

export interface Env {
  LICENSES: KVNamespace;
  PRODUCT_PREFIX: string;
  MAX_MACHINES: string;
  OFFLINE_GRACE_DAYS: string;
  HEARTBEAT_INTERVAL_DAYS: string;
  HMAC_SECRET: string;
  ADMIN_BEARER_TOKEN: string;

  // サポートセンター（Phase 1 = Resend 経由）
  // 未設定の場合は /support/* 系は 503 を返す
  RESEND_API_KEY?: string;
  SUPPORT_FROM_ADDRESS?: string;  // 例: "ClipGift サポート <onboarding@resend.dev>"
  SUPPORT_FORWARD_TO?: string;     // 例: "nekodori0612@gmail.com" (エラー報告 = Claude 自動処理ルート)
  // 要望（report_type=request）の転送先。未指定の場合は SUPPORT_FORWARD_TO にフォールバック。
  // 例: "clipgift.dev@gmail.com"（kyohei が手動で読んで返信する想定）
  SUPPORT_FORWARD_TO_REQUEST?: string;
  // Reply-To: ユーザーが「返信」を押したときに届く先（kyohei さん受信箱）
  // 未指定の場合は SUPPORT_FORWARD_TO を流用
  SUPPORT_REPLY_TO?: string;
}

// サポートエンドポイントの型
export interface SupportAttachment {
  /** ファイル名（例: "screenshot1.png"） */
  filename: string;
  /** Base64 エンコード済みのバイナリ */
  content_base64: string;
  /** MIME タイプ（例: "image/png"） */
  content_type: string;
}

export interface SupportReportRequest {
  app_version: string;
  os: string;
  license_tail: string;
  user_email: string;
  user_comment: string;
  error_log: string;
  env_info?: Record<string, unknown>;
  /** スクショ等の添付（type=error は必須、type=request は任意） */
  attachments?: SupportAttachment[];
  /** 種別: "error"（エラー報告、Claude 自動処理）/ "request"（ご要望、kyohei に直接） */
  report_type?: "error" | "request";
}

export interface SupportReportResponse {
  status: "ok";
  report_id: string;
  received_at: string;
}

export interface SupportNotifyRequest {
  error_hash: string;
  user_email: string;
  original_error_summary: string;
  repair_summary: string;
}

export interface SupportReplyRequest {
  to: string;
  subject: string;
  body: string;
  reply_to_hash?: string;
}

export interface SupportSendResponse {
  status: "ok";
  resend_id?: string;
}

export interface MachineRecord {
  fingerprint: string;
  label: string;
  activated_at: string;
  last_verified_at: string;
}

export interface LicenseRecord {
  key: string;
  plan: Plan;
  issued_at: string;
  expires_at: string | null;
  status: "unactivated" | "active" | "revoked";
  machines: MachineRecord[];
  order_source: "booth_auto" | "manual";
  order_id: string | null;
  buyer_email: string;
  support_expires_at: string;
  extension_expires_at: string | null;
  notes: string | null;
}

export interface ActivateRequest {
  key: string;
  machine_fingerprint: string;
  machine_label?: string;
}

export interface ActivateResponse {
  status: "ok";
  credential: string;
  plan: Plan;
  support_expires_at: string;
  extension_expires_at: string | null;
  machine_slot: number;
  next_verify_at: string;
}

export interface ErrorResponse {
  status: "error";
  code: ErrorCode;
  message: string;
  hint?: string;
}

export type ErrorCode =
  | "key_not_found"
  | "key_revoked"
  | "key_expired"
  | "max_machines_reached"
  | "signature_invalid"
  | "invalid_request"
  | "fingerprint_mismatch"
  | "unauthorized"
  | "internal_error";

export interface VerifyRequest {
  key: string;
  machine_fingerprint: string;
}

export interface VerifyResponse {
  status: "ok";
  credential_refresh: string;
  next_verify_at: string;
}

export interface DeactivateRequest {
  key: string;
  machine_fingerprint: string;
}

export interface DeactivateResponse {
  status: "ok";
  remaining_slots: number;
}

export interface AdminIssueRequest {
  plan: Plan;
  buyer_email: string;
  reason: string;
  order_id?: string;
  support_months?: number;
  extension_months?: number;
}

export interface AdminIssueResponse {
  status: "ok";
  key: string;
  expires_at: string | null;
  support_expires_at: string;
  extension_expires_at: string | null;
}

export interface AdminRevokeRequest {
  key: string;
  reason: string;
}

export interface AdminRevokeResponse {
  status: "ok";
  revoked_at: string;
}
