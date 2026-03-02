export interface FlagConfig {
  key: string;
  enabled: boolean;
  salt: string;
  rolloutPercentage: number; // 0-10000 (0.01% granularity)
  rules: TargetingRule[];
  variants: Variant[];
  payload?: unknown;
}

export interface TargetingRule {
  id: string;
  conditions: Condition[]; // AND logic within a rule
  variants: Variant[];
  rolloutPercentage: number;
}

export interface Condition {
  property: string; // "userId", "traits.plan", "country", etc.
  operator:
    | 'eq'
    | 'neq'
    | 'gt'
    | 'lt'
    | 'gte'
    | 'lte'
    | 'contains'
    | 'regex'
    | 'in'
    | 'not_in';
  value: unknown;
}

export interface Variant {
  name: string;
  weight: number; // sums to 10000 across variants
  payload?: unknown;
}

export interface FlagResult {
  value: boolean;
  variant: string | null;
  payload?: unknown;
  reason:
    | 'not_found'
    | 'disabled'
    | 'rule_match'
    | 'rollout'
    | 'not_in_rollout';
}

export interface EvalContext {
  userId?: string;
  anonymousId: string;
  traits?: Record<string, unknown>;
  groups?: Record<string, string>;
}
