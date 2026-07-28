const MAX_UI_URL_LENGTH = 4096;
const ABSOLUTE_HTTP_URL_PATTERN = /^https?:\/\//i;
const FORBIDDEN_URL_CHARACTER_PATTERN = /[\s\u0000-\u001f\u007f\\]/u;
const SAFE_UI_TARGETS = new Set(['_self', '_blank']);

/**
 * Canonical URL policy for every built-in UI URL sink.
 *
 * Only explicit absolute HTTP(S) URLs are accepted. Relative, protocol-
 * relative, credential-bearing, whitespace/control-obfuscated, and every
 * scriptable non-HTTP scheme (including javascript:, data:, and blob:) fail
 * closed before a DOM attribute is assigned.
 */
export function requireSafeUiUrl(value: unknown, path: string): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > MAX_UI_URL_LENGTH
    || !ABSOLUTE_HTTP_URL_PATTERN.test(value)
    || FORBIDDEN_URL_CHARACTER_PATTERN.test(value)
  ) {
    throw unsafeUrlError(path);
  }

  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw unsafeUrlError(path);
  }

  if (
    (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
    || parsed.hostname === ''
    || parsed.username !== ''
    || parsed.password !== ''
  ) {
    throw unsafeUrlError(path);
  }

  return parsed.href;
}

/**
 * Resolves one optional built-in UI URL.
 *
 * CMS and JSON-authored optional fields commonly arrive as null or an empty
 * string. Treat only those absent-like values as no URL; every nonempty value
 * still passes through the strict absolute HTTP(S) policy.
 */
export function optionalSafeUiUrl(value: unknown, path: string): string | null {
  if (value === undefined || value === null || value === '') {
    return null;
  }
  return requireSafeUiUrl(value, path);
}

export function requireSafeUiTarget(value: unknown, path: string): string {
  if (typeof value !== 'string' || !SAFE_UI_TARGETS.has(value)) {
    throw new Error(`APDL: ${path} must be one of: _self, _blank`);
  }
  return value;
}

function unsafeUrlError(path: string): Error {
  return new Error(`APDL: ${path} must be an absolute HTTP(S) URL`);
}
