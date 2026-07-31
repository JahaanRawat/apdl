# Weighted variant contract

APDL uses one weighted-variant contract in the Config service, JavaScript SDK,
Python SDK, and PostgreSQL:

- `MAX_VARIANTS = 10`
- `MAX_VARIANT_WEIGHT = 9_007_199_254_740_991`
- `MAX_TOTAL_VARIANT_WEIGHT = 9_007_199_254_740_991`

Every weight is a nonnegative integer that JavaScript can represent exactly.
The list is nonempty, its keys are unique, its default key exists, and its total
weight is positive without exceeding the total limit. Experiment authoring is
stricter: it requires 2–10 variants and every experiment weight is positive.

The executable cross-runtime vectors are in
`fixtures/gates/variant-weights.json`. The PostgreSQL baseline applies the same
bounds to `flags.variants` and `experiments.variants_json`, so invalid variant
configurations cannot enter a fresh database.
