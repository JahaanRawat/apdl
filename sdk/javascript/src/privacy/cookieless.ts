/**
 * Cookieless identity module.
 * Generates a daily-rotating anonymous ID based on SHA-256 hashing.
 * The ID is derived from browser fingerprint signals + the current date,
 * so it changes daily and cannot be used for long-term tracking.
 */
export class CookielessIdentity {
  private salt: string;

  constructor(salt: string) {
    this.salt = salt;
  }

  /**
   * Generates a daily-rotating anonymous ID.
   * Uses SHA-256 of (salt + date + fingerprint signals) to produce
   * a deterministic but non-persistent identifier.
   */
  async generateAnonymousId(): Promise<string> {
    const dateStr = this.getDateString();
    const fingerprint = this.collectFingerprint();
    const input = `${this.salt}:${dateStr}:${fingerprint}`;

    const hash = await this.sha256(input);
    return `cl_${hash}`;
  }

  /**
   * Returns today's date as YYYY-MM-DD for daily rotation.
   */
  private getDateString(): string {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  /**
   * Collects stable browser fingerprint signals.
   * These signals are not unique enough to identify a user on their own,
   * but combined with the salt provide reasonable short-term uniqueness.
   */
  private collectFingerprint(): string {
    const signals: string[] = [];

    if (typeof navigator !== 'undefined') {
      signals.push(navigator.userAgent || '');
      signals.push(navigator.language || '');
      signals.push(String(navigator.hardwareConcurrency || ''));
    }

    if (typeof screen !== 'undefined') {
      signals.push(`${screen.width}x${screen.height}`);
      signals.push(`${screen.colorDepth}`);
    }

    try {
      signals.push(Intl.DateTimeFormat().resolvedOptions().timeZone || '');
    } catch {
      // Intl not available
    }

    return signals.join('|');
  }

  /**
   * Computes SHA-256 hash using the Web Crypto API.
   * Falls back to a simple string hash if crypto is unavailable.
   */
  private async sha256(message: string): Promise<string> {
    if (
      typeof crypto !== 'undefined' &&
      crypto.subtle &&
      typeof crypto.subtle.digest === 'function'
    ) {
      const encoder = new TextEncoder();
      const data = encoder.encode(message);
      const hashBuffer = await crypto.subtle.digest('SHA-256', data);
      const hashArray = new Uint8Array(hashBuffer);
      return Array.from(hashArray)
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
    }

    // Fallback: simple hash for environments without Web Crypto
    return this.simpleHash(message);
  }

  /**
   * Simple string hash fallback (FNV-1a inspired, 128-bit output via concatenation).
   * NOT cryptographically secure — used only when Web Crypto is unavailable.
   */
  private simpleHash(str: string): string {
    let h1 = 0x811c9dc5;
    let h2 = 0x01000193;
    let h3 = 0xdeadbeef;
    let h4 = 0xcafebabe;

    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      h1 ^= c;
      h1 = Math.imul(h1, 0x01000193);
      h2 ^= c;
      h2 = Math.imul(h2, 0x0100019d);
      h3 ^= c;
      h3 = Math.imul(h3, 0x01000199);
      h4 ^= c;
      h4 = Math.imul(h4, 0x010001a3);
    }

    return [h1, h2, h3, h4]
      .map((h) => (h >>> 0).toString(16).padStart(8, '0'))
      .join('');
  }
}
