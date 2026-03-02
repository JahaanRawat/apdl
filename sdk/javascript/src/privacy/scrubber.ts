import type { TrackEvent } from '../core/types';

export type ScrubFunction = (event: TrackEvent) => TrackEvent | null;

// Email pattern: basic but catches most common formats
const EMAIL_PATTERN = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;

// Credit card: 13-19 digit sequences (with optional spaces/dashes)
const CREDIT_CARD_PATTERN =
  /\b(?:\d[ -]*?){13,19}\b/g;

// SSN: XXX-XX-XXXX pattern
const SSN_PATTERN = /\b\d{3}-\d{2}-\d{4}\b/g;

const REDACTED = '[REDACTED]';

/**
 * PII scrubbing pipeline.
 * Processes events through a chain of scrub functions.
 * Built-in scrubbers handle email, credit card, and SSN patterns.
 */
export class Scrubber {
  private pipeline: ScrubFunction[] = [];
  private builtInEnabled = true;

  constructor(enableBuiltIn = true) {
    this.builtInEnabled = enableBuiltIn;
    if (enableBuiltIn) {
      this.pipeline.push(scrubEmails, scrubCreditCards, scrubSSNs);
    }
  }

  /**
   * Adds a custom scrub function to the pipeline.
   * Functions are executed in order. Return null to drop the event entirely.
   */
  addScrubber(fn: ScrubFunction): void {
    this.pipeline.push(fn);
  }

  /**
   * Removes a scrub function from the pipeline.
   */
  removeScrubber(fn: ScrubFunction): void {
    const index = this.pipeline.indexOf(fn);
    if (index !== -1) {
      this.pipeline.splice(index, 1);
    }
  }

  /**
   * Runs an event through the scrubbing pipeline.
   * Returns the scrubbed event, or null if any scrubber drops it.
   */
  scrub(event: TrackEvent): TrackEvent | null {
    // Deep clone to avoid mutating the original
    let current: TrackEvent | null = deepClone(event);

    for (const fn of this.pipeline) {
      if (current === null) return null;
      current = fn(current);
    }

    return current;
  }

  /**
   * Returns the number of scrub functions in the pipeline.
   */
  get size(): number {
    return this.pipeline.length;
  }
}

/**
 * Built-in scrubber: replaces email addresses in string values.
 */
function scrubEmails(event: TrackEvent): TrackEvent {
  return scrubStringValues(event, (value) =>
    value.replace(EMAIL_PATTERN, REDACTED)
  );
}

/**
 * Built-in scrubber: replaces credit card numbers in string values.
 */
function scrubCreditCards(event: TrackEvent): TrackEvent {
  return scrubStringValues(event, (value) =>
    value.replace(CREDIT_CARD_PATTERN, REDACTED)
  );
}

/**
 * Built-in scrubber: replaces SSN patterns in string values.
 */
function scrubSSNs(event: TrackEvent): TrackEvent {
  return scrubStringValues(event, (value) =>
    value.replace(SSN_PATTERN, REDACTED)
  );
}

/**
 * Applies a string transformation to all string values in an event's
 * properties and traits (recursively).
 */
function scrubStringValues(
  event: TrackEvent,
  transform: (value: string) => string
): TrackEvent {
  if (event.properties) {
    event.properties = scrubObject(event.properties, transform);
  }
  if (event.traits) {
    event.traits = scrubObject(event.traits, transform);
  }
  return event;
}

function scrubObject(
  obj: Record<string, unknown>,
  transform: (value: string) => string
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'string') {
      result[key] = transform(value);
    } else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      result[key] = scrubObject(
        value as Record<string, unknown>,
        transform
      );
    } else if (Array.isArray(value)) {
      result[key] = value.map((item) => {
        if (typeof item === 'string') return transform(item);
        if (item !== null && typeof item === 'object') {
          return scrubObject(item as Record<string, unknown>, transform);
        }
        return item;
      });
    } else {
      result[key] = value;
    }
  }
  return result;
}

function deepClone<T>(obj: T): T {
  if (obj === null || typeof obj !== 'object') return obj;
  if (Array.isArray(obj)) {
    return obj.map((item) => deepClone(item)) as unknown as T;
  }
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    result[key] = deepClone(value);
  }
  return result as T;
}
