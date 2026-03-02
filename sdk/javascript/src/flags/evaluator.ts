import type {
  FlagConfig,
  FlagResult,
  EvalContext,
  TargetingRule,
  Condition,
  Variant,
} from './types';
import { FlagCache } from './cache';
import { murmurhash3 } from './hash';

/**
 * Feature flag evaluation engine.
 * Evaluates flags against targeting rules and rollout percentages
 * using deterministic hashing for consistent bucketing.
 */
export class FlagEvaluator {
  private cache: FlagCache;

  constructor(cache: FlagCache) {
    this.cache = cache;
  }

  /**
   * Evaluates a feature flag against the given context.
   *
   * Evaluation order:
   * 1. Check if flag exists -> not_found
   * 2. Check if flag is enabled -> disabled
   * 3. Check targeting rules in order (first match wins)
   * 4. Fall through to default rollout percentage
   */
  evaluate(key: string, context: EvalContext): FlagResult {
    const flag = this.cache.get(key);

    if (!flag) {
      return {
        value: false,
        variant: null,
        reason: 'not_found',
      };
    }

    if (!flag.enabled) {
      return {
        value: false,
        variant: null,
        reason: 'disabled',
      };
    }

    // Check targeting rules in order (priority)
    for (const rule of flag.rules) {
      if (this.matchesRule(rule, context)) {
        // Check rule-level rollout
        const userKey = context.userId || context.anonymousId;
        const ruleHash = murmurhash3(`${flag.salt}:${rule.id}:${userKey}`) % 10000;

        if (ruleHash < rule.rolloutPercentage) {
          const variant = this.selectVariant(
            rule.variants.length > 0 ? rule.variants : flag.variants,
            flag.salt,
            userKey,
            key
          );
          return {
            value: true,
            variant: variant?.name ?? null,
            payload: variant?.payload ?? flag.payload,
            reason: 'rule_match',
          };
        }

        // Rule matched but user is not in the rollout
        return {
          value: false,
          variant: null,
          reason: 'not_in_rollout',
        };
      }
    }

    // Default rollout
    const userKey = context.userId || context.anonymousId;
    const hash = murmurhash3(`${flag.salt}:${userKey}`) % 10000;

    if (hash < flag.rolloutPercentage) {
      const variant = this.selectVariant(flag.variants, flag.salt, userKey, key);
      return {
        value: true,
        variant: variant?.name ?? null,
        payload: variant?.payload ?? flag.payload,
        reason: 'rollout',
      };
    }

    return {
      value: false,
      variant: null,
      reason: 'not_in_rollout',
    };
  }

  /**
   * Checks if all conditions in a targeting rule match (AND logic).
   */
  private matchesRule(rule: TargetingRule, context: EvalContext): boolean {
    if (rule.conditions.length === 0) return true;
    return rule.conditions.every((condition) =>
      this.evaluateCondition(condition, context)
    );
  }

  /**
   * Evaluates a single condition against the context.
   */
  private evaluateCondition(condition: Condition, context: EvalContext): boolean {
    const actual = this.resolveProperty(condition.property, context);
    const expected = condition.value;

    switch (condition.operator) {
      case 'eq':
        return actual === expected;

      case 'neq':
        return actual !== expected;

      case 'gt':
        return typeof actual === 'number' && typeof expected === 'number' && actual > expected;

      case 'lt':
        return typeof actual === 'number' && typeof expected === 'number' && actual < expected;

      case 'gte':
        return typeof actual === 'number' && typeof expected === 'number' && actual >= expected;

      case 'lte':
        return typeof actual === 'number' && typeof expected === 'number' && actual <= expected;

      case 'contains':
        return typeof actual === 'string' && typeof expected === 'string' && actual.includes(expected);

      case 'regex': {
        if (typeof actual !== 'string' || typeof expected !== 'string') return false;
        try {
          return new RegExp(expected).test(actual);
        } catch {
          return false;
        }
      }

      case 'in':
        return Array.isArray(expected) && expected.includes(actual);

      case 'not_in':
        return Array.isArray(expected) && !expected.includes(actual);

      default:
        return false;
    }
  }

  /**
   * Resolves a dotted property path from the evaluation context.
   * Examples: "userId", "traits.plan", "groups.company"
   */
  private resolveProperty(property: string, context: EvalContext): unknown {
    const parts = property.split('.');

    if (parts[0] === 'userId') return context.userId;
    if (parts[0] === 'anonymousId') return context.anonymousId;

    if (parts[0] === 'traits' && context.traits) {
      return this.getNestedValue(context.traits, parts.slice(1));
    }

    if (parts[0] === 'groups' && context.groups) {
      return this.getNestedValue(context.groups, parts.slice(1));
    }

    // Direct lookup in traits as fallback
    if (context.traits && parts.length === 1) {
      return context.traits[parts[0]];
    }

    return undefined;
  }

  private getNestedValue(
    obj: Record<string, unknown>,
    path: string[]
  ): unknown {
    let current: unknown = obj;
    for (const key of path) {
      if (current === null || current === undefined || typeof current !== 'object') {
        return undefined;
      }
      current = (current as Record<string, unknown>)[key];
    }
    return current;
  }

  /**
   * Selects a variant using weighted deterministic bucketing.
   * Hash maps to a value 0-9999; walk through variants by cumulative weight.
   */
  private selectVariant(
    variants: Variant[],
    salt: string,
    userKey: string,
    flagKey: string
  ): Variant | null {
    if (variants.length === 0) return null;
    if (variants.length === 1) return variants[0];

    const hash = murmurhash3(`${salt}:variant:${flagKey}:${userKey}`) % 10000;
    let cumulative = 0;

    for (const variant of variants) {
      cumulative += variant.weight;
      if (hash < cumulative) {
        return variant;
      }
    }

    // Should not happen if weights sum to 10000, but return last variant as safety
    return variants[variants.length - 1];
  }
}
