/**
 * MurmurHash3 (32-bit) implementation in pure TypeScript.
 * Produces deterministic hashes matching the reference C implementation.
 *
 * Based on the MurmurHash3_x86_32 algorithm by Austin Appleby.
 * Public domain.
 */
export function murmurhash3(key: string, seed: number = 0): number {
  let h1 = seed >>> 0;
  const len = key.length;

  const c1 = 0xcc9e2d51;
  const c2 = 0x1b873593;

  // Process the body (4-byte blocks)
  const nblocks = len >> 2;

  for (let i = 0; i < nblocks; i++) {
    const i4 = i * 4;
    let k1 =
      (key.charCodeAt(i4) & 0xff) |
      ((key.charCodeAt(i4 + 1) & 0xff) << 8) |
      ((key.charCodeAt(i4 + 2) & 0xff) << 16) |
      ((key.charCodeAt(i4 + 3) & 0xff) << 24);

    k1 = Math.imul(k1, c1);
    k1 = (k1 << 15) | (k1 >>> 17);
    k1 = Math.imul(k1, c2);

    h1 ^= k1;
    h1 = (h1 << 13) | (h1 >>> 19);
    h1 = Math.imul(h1, 5) + 0xe6546b64;
  }

  // Process the tail
  const tail = nblocks * 4;
  let k1 = 0;

  switch (len & 3) {
    case 3:
      k1 ^= (key.charCodeAt(tail + 2) & 0xff) << 16;
    // falls through
    case 2:
      k1 ^= (key.charCodeAt(tail + 1) & 0xff) << 8;
    // falls through
    case 1:
      k1 ^= key.charCodeAt(tail) & 0xff;
      k1 = Math.imul(k1, c1);
      k1 = (k1 << 15) | (k1 >>> 17);
      k1 = Math.imul(k1, c2);
      h1 ^= k1;
  }

  // Finalization mix
  h1 ^= len;
  h1 = fmix32(h1);

  return h1 >>> 0;
}

/**
 * Finalization mix — forces all bits of a hash block to avalanche.
 */
function fmix32(h: number): number {
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h;
}
