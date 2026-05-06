/**
 * クリップギフト ライセンスサーバー 型定義
 */

export type Plan = "lite" | "std" | "ext";

export interface Env {
  LICENSES: KVNamespace;
  PRODUCT_PREFIX: string;
  MAX_MACHINES: string;
  OFFLINE_GRACE_DAYS: string;
  HEARTBEAT_INTERVAL_DAYS: string;
  HMAC_SECRET: string;
  ADMIN_BEARER_TOKEN: string;
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
