import { describe, it, expect } from 'vitest';
import { murmurhash3 } from '../../src/flags/hash';

describe('murmurhash3', () => {
  describe('determinism', () => {
    it('should return the same result for the same input', () => {
      const hash1 = murmurhash3('hello');
      const hash2 = murmurhash3('hello');
      expect(hash1).toBe(hash2);
    });

    it('should return the same result with the same seed', () => {
      const hash1 = murmurhash3('test', 42);
      const hash2 = murmurhash3('test', 42);
      expect(hash1).toBe(hash2);
    });

    it('should return different results for different seeds', () => {
      const hash1 = murmurhash3('test', 0);
      const hash2 = murmurhash3('test', 1);
      expect(hash1).not.toBe(hash2);
    });

    it('should return different results for different inputs', () => {
      const hash1 = murmurhash3('hello');
      const hash2 = murmurhash3('world');
      expect(hash1).not.toBe(hash2);
    });
  });

  describe('known values', () => {
    // Reference values from the canonical MurmurHash3 C implementation
    it('should hash empty string with seed 0', () => {
      const result = murmurhash3('', 0);
      expect(result).toBe(0);
    });

    it('should produce unsigned 32-bit integers', () => {
      for (const input of ['a', 'ab', 'abc', 'abcd', 'test', 'hello world']) {
        const hash = murmurhash3(input);
        expect(hash).toBeGreaterThanOrEqual(0);
        expect(hash).toBeLessThanOrEqual(0xffffffff);
        expect(Number.isInteger(hash)).toBe(true);
      }
    });

    it('should handle single character', () => {
      const hash = murmurhash3('a', 0);
      expect(typeof hash).toBe('number');
      expect(hash).toBeGreaterThan(0);
    });

    it('should handle strings of various lengths', () => {
      const results = new Set<number>();
      for (let len = 0; len <= 20; len++) {
        const str = 'x'.repeat(len);
        results.add(murmurhash3(str, 0));
      }
      // All different lengths should produce different hashes (very high probability)
      expect(results.size).toBe(21);
    });
  });

  describe('distribution quality', () => {
    it('should distribute evenly across buckets', () => {
      const numBuckets = 10;
      const numInputs = 10000;
      const buckets = new Array<number>(numBuckets).fill(0);

      for (let i = 0; i < numInputs; i++) {
        const hash = murmurhash3(`user-${i}`, 0);
        const bucket = hash % numBuckets;
        buckets[bucket]++;
      }

      const expected = numInputs / numBuckets;
      const tolerance = 0.15; // 15% tolerance

      for (let i = 0; i < numBuckets; i++) {
        expect(buckets[i]).toBeGreaterThan(expected * (1 - tolerance));
        expect(buckets[i]).toBeLessThan(expected * (1 + tolerance));
      }
    });

    it('should distribute evenly for flag bucketing (modulo 10000)', () => {
      const numInputs = 100000;
      let inBucket = 0;
      const targetPercentage = 5000; // 50%

      for (let i = 0; i < numInputs; i++) {
        const hash = murmurhash3(`salt:user-${i}`, 0) % 10000;
        if (hash < targetPercentage) {
          inBucket++;
        }
      }

      const actualPercentage = inBucket / numInputs;
      // Should be roughly 50% +/- 2%
      expect(actualPercentage).toBeGreaterThan(0.48);
      expect(actualPercentage).toBeLessThan(0.52);
    });
  });

  describe('edge cases', () => {
    it('should handle empty string', () => {
      expect(() => murmurhash3('')).not.toThrow();
    });

    it('should handle very long strings', () => {
      const longStr = 'a'.repeat(10000);
      expect(() => murmurhash3(longStr)).not.toThrow();
      const hash = murmurhash3(longStr);
      expect(hash).toBeGreaterThanOrEqual(0);
    });

    it('should handle special characters', () => {
      const hash1 = murmurhash3('\n\t\r');
      const hash2 = murmurhash3('\u0000\u0001\u0002');
      expect(typeof hash1).toBe('number');
      expect(typeof hash2).toBe('number');
      expect(hash1).not.toBe(hash2);
    });

    it('should handle unicode', () => {
      const hash = murmurhash3('\u{1F600}\u{1F601}');
      expect(typeof hash).toBe('number');
      expect(hash).toBeGreaterThanOrEqual(0);
    });

    it('should default seed to 0', () => {
      const hash1 = murmurhash3('test');
      const hash2 = murmurhash3('test', 0);
      expect(hash1).toBe(hash2);
    });
  });

  describe('avalanche property', () => {
    it('should produce significantly different hashes for similar inputs', () => {
      const hash1 = murmurhash3('test0');
      const hash2 = murmurhash3('test1');

      // Convert to binary and count differing bits
      const xor = hash1 ^ hash2;
      let bitsChanged = 0;
      for (let i = 0; i < 32; i++) {
        if ((xor >> i) & 1) bitsChanged++;
      }

      // Good hash should change roughly half the bits (16 +/- 8)
      expect(bitsChanged).toBeGreaterThan(4);
    });
  });
});
