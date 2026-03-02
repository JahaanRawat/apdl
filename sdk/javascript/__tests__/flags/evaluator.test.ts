import { describe, it, expect, beforeEach } from 'vitest';
import { FlagEvaluator } from '../../src/flags/evaluator';
import { FlagCache } from '../../src/flags/cache';
import type { FlagConfig, EvalContext } from '../../src/flags/types';

describe('FlagEvaluator', () => {
  let cache: FlagCache;
  let evaluator: FlagEvaluator;

  const baseContext: EvalContext = {
    userId: 'user-1',
    anonymousId: 'anon-1',
    traits: { plan: 'pro', country: 'US', age: 30 },
    groups: { company: 'acme' },
  };

  beforeEach(() => {
    cache = new FlagCache();
    evaluator = new FlagEvaluator(cache);
  });

  describe('basic evaluation', () => {
    it('should return not_found for unknown flags', () => {
      const result = evaluator.evaluate('nonexistent', baseContext);
      expect(result).toEqual({
        value: false,
        variant: null,
        reason: 'not_found',
      });
    });

    it('should return disabled for disabled flags', () => {
      const flag: FlagConfig = {
        key: 'my-flag',
        enabled: false,
        salt: 'salt1',
        rolloutPercentage: 10000,
        rules: [],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('my-flag', baseContext);
      expect(result.value).toBe(false);
      expect(result.reason).toBe('disabled');
    });

    it('should return true for 100% rollout', () => {
      const flag: FlagConfig = {
        key: 'full-rollout',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 10000, // 100%
        rules: [],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('full-rollout', baseContext);
      expect(result.value).toBe(true);
      expect(result.reason).toBe('rollout');
    });

    it('should return false for 0% rollout', () => {
      const flag: FlagConfig = {
        key: 'no-rollout',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('no-rollout', baseContext);
      expect(result.value).toBe(false);
      expect(result.reason).toBe('not_in_rollout');
    });
  });

  describe('deterministic bucketing', () => {
    it('should produce consistent results for the same user', () => {
      const flag: FlagConfig = {
        key: 'sticky-flag',
        enabled: true,
        salt: 'consistent-salt',
        rolloutPercentage: 5000, // 50%
        rules: [],
        variants: [],
      };
      cache.set([flag]);

      const result1 = evaluator.evaluate('sticky-flag', baseContext);
      const result2 = evaluator.evaluate('sticky-flag', baseContext);
      const result3 = evaluator.evaluate('sticky-flag', baseContext);

      expect(result1.value).toBe(result2.value);
      expect(result2.value).toBe(result3.value);
    });

    it('should produce different results for different users at 50% rollout', () => {
      const flag: FlagConfig = {
        key: 'split-flag',
        enabled: true,
        salt: 'split-salt',
        rolloutPercentage: 5000,
        rules: [],
        variants: [],
      };
      cache.set([flag]);

      const results = new Set<boolean>();
      for (let i = 0; i < 100; i++) {
        const ctx: EvalContext = {
          anonymousId: `anon-${i}`,
          userId: `user-${i}`,
        };
        const result = evaluator.evaluate('split-flag', ctx);
        results.add(result.value);
      }

      // With 100 users at 50%, we should see both true and false
      expect(results.size).toBe(2);
    });
  });

  describe('targeting rules', () => {
    it('should match eq condition', () => {
      const flag: FlagConfig = {
        key: 'targeted',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0, // default off
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'pro' },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('targeted', baseContext);
      expect(result.value).toBe(true);
      expect(result.reason).toBe('rule_match');
    });

    it('should not match when condition fails', () => {
      const flag: FlagConfig = {
        key: 'targeted',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'enterprise' },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('targeted', baseContext);
      expect(result.value).toBe(false);
      expect(result.reason).toBe('not_in_rollout');
    });

    it('should apply AND logic within a rule', () => {
      const flag: FlagConfig = {
        key: 'multi-condition',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'pro' },
              { property: 'traits.country', operator: 'eq', value: 'US' },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('multi-condition', baseContext);
      expect(result.value).toBe(true);
      expect(result.reason).toBe('rule_match');

      // Fails when one condition doesn't match
      const otherContext: EvalContext = {
        userId: 'user-2',
        anonymousId: 'anon-2',
        traits: { plan: 'pro', country: 'UK' },
      };
      const result2 = evaluator.evaluate('multi-condition', otherContext);
      expect(result2.value).toBe(false);
    });

    it('should check rules in order and stop at first match', () => {
      const flag: FlagConfig = {
        key: 'priority-rules',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'enterprise' },
            ],
            variants: [{ name: 'enterprise-variant', weight: 10000 }],
            rolloutPercentage: 10000,
          },
          {
            id: 'rule-2',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'pro' },
            ],
            variants: [{ name: 'pro-variant', weight: 10000 }],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([flag]);

      // user with plan=pro should match rule-2
      const result = evaluator.evaluate('priority-rules', baseContext);
      expect(result.value).toBe(true);
      expect(result.variant).toBe('pro-variant');
    });
  });

  describe('operators', () => {
    const makeFlag = (operator: string, value: unknown): FlagConfig => ({
      key: 'op-test',
      enabled: true,
      salt: 'salt1',
      rolloutPercentage: 0,
      rules: [
        {
          id: 'rule-1',
          conditions: [
            {
              property: 'traits.age',
              operator: operator as 'eq',
              value,
            },
          ],
          variants: [],
          rolloutPercentage: 10000,
        },
      ],
      variants: [],
    });

    it('should handle neq operator', () => {
      cache.set([makeFlag('neq', 25)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(true);
    });

    it('should handle gt operator', () => {
      cache.set([makeFlag('gt', 25)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(true);

      cache.set([makeFlag('gt', 35)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(false);
    });

    it('should handle lt operator', () => {
      cache.set([makeFlag('lt', 35)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(true);

      cache.set([makeFlag('lt', 25)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(false);
    });

    it('should handle gte operator', () => {
      cache.set([makeFlag('gte', 30)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(true);

      cache.set([makeFlag('gte', 31)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(false);
    });

    it('should handle lte operator', () => {
      cache.set([makeFlag('lte', 30)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(true);

      cache.set([makeFlag('lte', 29)]);
      expect(evaluator.evaluate('op-test', baseContext).value).toBe(false);
    });

    it('should handle contains operator', () => {
      const containsFlag: FlagConfig = {
        key: 'contains-test',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.country', operator: 'contains', value: 'U' },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([containsFlag]);
      expect(evaluator.evaluate('contains-test', baseContext).value).toBe(true);
    });

    it('should handle regex operator', () => {
      const regexFlag: FlagConfig = {
        key: 'regex-test',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'regex', value: '^pro$' },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([regexFlag]);
      expect(evaluator.evaluate('regex-test', baseContext).value).toBe(true);
    });

    it('should handle in operator', () => {
      const inFlag: FlagConfig = {
        key: 'in-test',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              {
                property: 'traits.country',
                operator: 'in',
                value: ['US', 'CA', 'UK'],
              },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([inFlag]);
      expect(evaluator.evaluate('in-test', baseContext).value).toBe(true);
    });

    it('should handle not_in operator', () => {
      const notInFlag: FlagConfig = {
        key: 'notin-test',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              {
                property: 'traits.country',
                operator: 'not_in',
                value: ['DE', 'FR'],
              },
            ],
            variants: [],
            rolloutPercentage: 10000,
          },
        ],
        variants: [],
      };
      cache.set([notInFlag]);
      expect(evaluator.evaluate('notin-test', baseContext).value).toBe(true);
    });
  });

  describe('variant selection', () => {
    it('should select a variant from weighted distribution', () => {
      const flag: FlagConfig = {
        key: 'ab-test',
        enabled: true,
        salt: 'variant-salt',
        rolloutPercentage: 10000,
        rules: [],
        variants: [
          { name: 'control', weight: 5000 },
          { name: 'treatment', weight: 5000 },
        ],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('ab-test', baseContext);
      expect(result.value).toBe(true);
      expect(result.variant).toBeDefined();
      expect(['control', 'treatment']).toContain(result.variant);
    });

    it('should distribute variants across users', () => {
      const flag: FlagConfig = {
        key: 'distribution-test',
        enabled: true,
        salt: 'dist-salt',
        rolloutPercentage: 10000,
        rules: [],
        variants: [
          { name: 'A', weight: 5000 },
          { name: 'B', weight: 5000 },
        ],
      };
      cache.set([flag]);

      const variantCounts: Record<string, number> = { A: 0, B: 0 };
      for (let i = 0; i < 1000; i++) {
        const ctx: EvalContext = {
          anonymousId: `anon-${i}`,
          userId: `user-${i}`,
        };
        const result = evaluator.evaluate('distribution-test', ctx);
        if (result.variant) {
          variantCounts[result.variant]++;
        }
      }

      // With 1000 users split 50/50, each should be roughly 500
      // Allow wide tolerance for hash-based distribution
      expect(variantCounts.A).toBeGreaterThan(300);
      expect(variantCounts.B).toBeGreaterThan(300);
    });

    it('should include variant payload', () => {
      const flag: FlagConfig = {
        key: 'payload-test',
        enabled: true,
        salt: 'payload-salt',
        rolloutPercentage: 10000,
        rules: [],
        variants: [
          {
            name: 'only-variant',
            weight: 10000,
            payload: { color: 'blue', size: 'large' },
          },
        ],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('payload-test', baseContext);
      expect(result.value).toBe(true);
      expect(result.variant).toBe('only-variant');
      expect(result.payload).toEqual({ color: 'blue', size: 'large' });
    });
  });

  describe('rule-level rollout', () => {
    it('should respect rule rollout percentage', () => {
      const flag: FlagConfig = {
        key: 'rule-rollout',
        enabled: true,
        salt: 'rule-rollout-salt',
        rolloutPercentage: 0,
        rules: [
          {
            id: 'rule-1',
            conditions: [
              { property: 'traits.plan', operator: 'eq', value: 'pro' },
            ],
            variants: [],
            rolloutPercentage: 0, // Rule matches but 0% rollout
          },
        ],
        variants: [],
      };
      cache.set([flag]);

      const result = evaluator.evaluate('rule-rollout', baseContext);
      expect(result.value).toBe(false);
      expect(result.reason).toBe('not_in_rollout');
    });
  });

  describe('flag payload', () => {
    it('should return flag-level payload when no variant payload exists', () => {
      const flag: FlagConfig = {
        key: 'flag-payload',
        enabled: true,
        salt: 'salt1',
        rolloutPercentage: 10000,
        rules: [],
        variants: [{ name: 'v1', weight: 10000 }],
        payload: { globalConfig: true },
      };
      cache.set([flag]);

      const result = evaluator.evaluate('flag-payload', baseContext);
      expect(result.payload).toEqual({ globalConfig: true });
    });
  });
});
