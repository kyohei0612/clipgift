/**
 * 受信メール分類のテスト。
 *
 * 主眼は「承認のなりすまし」を防げているか。
 * reply_to_secretary はローカル watcher 側で
 * 「kyohei が承認した」→ ユーザーへ自動返信、という流れに繋がるため、
 * **件名だけで到達できてはいけない**。
 */
import { describe, expect, it } from "vitest";
import { classifyMessage, isTrustedOperator } from "../src/handlers/incoming_mail";
import type { Env } from "../src/types";

const env = {
  SUPPORT_FORWARD_TO: "nekodori0612@gmail.com",
  SUPPORT_FORWARD_TO_REQUEST: "clipgift.dev@gmail.com",
  SUPPORT_REPLY_TO: undefined,
} as unknown as Env;

function classify(subject: string, from: string) {
  return classifyMessage({
    subject,
    body: "",
    from,
    trustedOperator: isTrustedOperator(from, env),
  });
}

describe("isTrustedOperator", () => {
  it("運営アドレスを信頼する", () => {
    expect(isTrustedOperator("nekodori0612@gmail.com", env)).toBe(true);
    expect(isTrustedOperator("clipgift.dev@gmail.com", env)).toBe(true);
  });

  it("表示名付き / 大文字混じりでも正しく判定する", () => {
    expect(isTrustedOperator("kyohei <NekoDori0612@Gmail.com>", env)).toBe(true);
    expect(isTrustedOperator("  nekodori0612@gmail.com  ", env)).toBe(true);
  });

  it("外部アドレスは信頼しない", () => {
    expect(isTrustedOperator("attacker@evil.example", env)).toBe(false);
    expect(isTrustedOperator("", env)).toBe(false);
    expect(isTrustedOperator("unknown", env)).toBe(false);
  });

  it("部分一致で通してしまわない", () => {
    // 前方/後方に付け足したアドレスで擦り抜けないこと
    expect(isTrustedOperator("xnekodori0612@gmail.com", env)).toBe(false);
    expect(isTrustedOperator("nekodori0612@gmail.com.evil.example", env)).toBe(false);
  });
});

describe("承認のなりすまし防止", () => {
  const confirmSubject = "Re: 【ClipGift 確認依頼】 0123456789ab";
  const reviewSubject = "Re: 【ClipGift 修正案レビュー依頼】 0123456789ab";

  it("運営からの確認依頼への返信は reply_to_secretary になる", () => {
    const r = classify(confirmSubject, "nekodori0612@gmail.com");
    expect(r.triggerType).toBe("reply_to_secretary");
    expect(r.errorHash).toBe("0123456789ab");
  });

  it("運営からのレビュー依頼への返信も reply_to_secretary", () => {
    expect(classify(reviewSubject, "clipgift.dev@gmail.com").triggerType).toBe(
      "reply_to_secretary"
    );
  });

  it("**外部から同じ件名を送っても reply_to_secretary にならない**", () => {
    expect(classify(confirmSubject, "attacker@evil.example").triggerType).toBe("ignore");
    expect(classify(reviewSubject, "attacker@evil.example").triggerType).toBe("ignore");
  });

  it("外部からのエラー報告への Re: も承認扱いにしない", () => {
    const s = "Re: 【ClipGift エラー報告】v2.0.1 - ValueError";
    expect(classify(s, "attacker@evil.example").triggerType).toBe("ignore");
    expect(classify(s, "nekodori0612@gmail.com").triggerType).toBe("reply_to_secretary");
  });

  it("送信元不明（from が取れない）でも承認扱いにしない", () => {
    expect(classify(confirmSubject, "unknown").triggerType).toBe("ignore");
  });
});

describe("新規メールの分類（従来どおり）", () => {
  it("エラー報告は incoming_error", () => {
    expect(
      classify("【ClipGift エラー報告】v2.0.1 - ValueError", "user@example.com").triggerType
    ).toBe("incoming_error");
  });

  it("ご要望は incoming_request", () => {
    expect(
      classify("【ClipGift ご要望】v2.0.1 - 縦動画対応", "user@example.com").triggerType
    ).toBe("incoming_request");
  });

  it("自社送信のループ（Re: でない自社件名）は ignore", () => {
    expect(
      classify("【ClipGift 確認依頼】 0123456789ab", "nekodori0612@gmail.com").triggerType
    ).toBe("ignore");
    expect(
      classify("【ClipGift 要望通知】v2.0.1", "nekodori0612@gmail.com").triggerType
    ).toBe("ignore");
  });

  it("無関係なメールは ignore", () => {
    expect(classify("こんにちは", "user@example.com").triggerType).toBe("ignore");
  });

  it("エラー報告の件名は**外部からでも**受け付ける（ユーザー起点なので正しい）", () => {
    // なりすまし対策で塞ぐのは承認系だけ。報告の受付まで塞ぐと通常運用が壊れる。
    expect(
      classify("【ClipGift エラー報告】v1.0", "anyone@example.com").triggerType
    ).toBe("incoming_error");
  });
});
