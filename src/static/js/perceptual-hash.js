/**
 * Perceptual Hash Matching Service (RGB-aware DCT-based)
 * Ported from 1vcian/Pokemon-TCGP-Card-Scanner ImageHashingService.ts
 * Runs entirely in-browser, no server dependency
 */
export class PerceptualHashMatcher {
  constructor(hashSize = 24) {
    this.hashSize = hashSize;
    this.cardHashes = new Map(); // cardId -> { hash, name, set, number, rarity, image }
    this.indexLoaded = false;
    // The hashes again, packed as integers (see _buildPackedIndex). Matching a
    // capture used to walk the index as strings, build one result object per
    // card and sort all ~40k of them, which is what made a capture crawl on a
    // tablet.
    this._packed = null;
    this._packedIds = null;
    this._packedLens = null;
    this._words = 4;
    this._cos = null;
  }

  /**
   * Load the card index with precomputed hashes
   *
   * A card may carry `hash: null` when loadPackedIndex() already supplied the
   * hashes: its metadata is kept so match results can name the card, and
   * nothing is packed from strings.
   * @param {Array} cards - Array of { id, name, set, number, rarity, image, hash }
   */
  loadIndex(cards) {
    this.cardHashes.clear();
    let hashed = 0;
    for (const card of cards) {
      this.cardHashes.set(card.id, {
        hash: card.hash || null,
        name: card.name,
        set: card.set,
        number: card.number,
        rarity: card.rarity,
        image: card.image,
      });
      if (card.hash) hashed += 1;
    }
    this.indexLoaded = true;
    // A blob index arrives through loadPackedIndex(), which main.js calls
    // first, so this call usually carries no hashes at all, and packing then
    // would replace the blob's words with an empty index: the guard is
    // load-order protection, not an optimisation. Building from strings is
    // what a page served without card-hashes.bin still does.
    if (hashed) this._buildPackedIndex();
    console.log(`[PerceptualHash] Loaded ${this.cardHashes.size} cards, ${hashed} hashes`);
  }

  /**
   * Pack the index into 32-bit words, once per load.
   *
   * 105 bits is four words, so a comparison is four XOR+popcounts against
   * integers instead of 105 one-character string reads. The per-card hash
   * length is kept as well: the string comparison this replaces added the
   * length difference to the count, and that must not silently change.
   */
  _buildPackedIndex() {
    let count = 0;
    let hashLength = 0;
    for (const card of this.cardHashes.values()) {
      if (!card.hash) continue;
      // The longest hash sizes the words, the rule pack_hash_index.py applies;
      // the first card's length would truncate a longer hash and stop a JSON
      // build matching the blob bit for bit.
      if (card.hash.length > hashLength) hashLength = card.hash.length;
      count += 1;
    }
    const words = Math.max(1, Math.ceil(hashLength / 32));
    const packed = new Uint32Array(count * words);
    const ids = new Array(count);
    const lens = new Uint16Array(count);
    let i = 0;
    for (const [id, card] of this.cardHashes) {
      if (!card.hash) continue;
      const hash = card.hash;
      const row = i * words;
      const bits = Math.min(hash.length, words * 32);
      for (let b = 0; b < bits; b++) {
        if (hash.charCodeAt(b) === 49) packed[row + (b >> 5)] |= 1 << (b & 31);
      }
      ids[i] = id;
      lens[i] = hash.length;
      i++;
    }
    this._packed = packed;
    this._packedIds = ids;
    this._packedLens = lens;
    this._words = words;
  }

  /**
   * Load the packed index the build step ships (card-hashes.bin). The format
   * is defined, and only defined, in scripts/pack_hash_index.py.
   *
   * These are the same words _buildPackedIndex() derives from 40k hash
   * strings, already packed: four typed-array views over the fetched bytes, no
   * per-bit work and no strings to keep alive. Returns the number of cards so
   * a caller can fall back to the JSON index when this throws.
   */
  loadPackedIndex(buffer) {
    const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 4));
    if (magic !== 'PHX1') throw new Error(`packed hash index: bad magic ${magic}`);
    const view = new DataView(buffer);
    const count = view.getUint32(4, true);
    const words = view.getUint32(8, true);
    const bits = view.getUint32(12, true);
    if (words !== Math.max(1, Math.ceil(bits / 32))) {
      throw new Error(`packed hash index: ${words} words for ${bits} bits`);
    }
    let offset = 16;
    const packed = new Uint32Array(buffer, offset, count * words);
    offset += count * words * 4;
    const lens = new Uint16Array(buffer, offset, count);
    offset += count * 2;
    const ids = new TextDecoder().decode(new Uint8Array(buffer, offset)).split('\n');
    if (ids.length !== count) {
      throw new Error(`packed hash index: ${ids.length} ids for ${count} cards`);
    }
    this._packed = packed;
    this._packedIds = ids;
    this._packedLens = lens;
    this._words = words;
    return count;
  }

  /**
   * Calculate 24-bit RGB-aware perceptual hash for an image element
   */
  async calculateHash(imageElement) {
    return new Promise((resolve, reject) => {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');

      canvas.width = this.hashSize;
      canvas.height = this.hashSize;

      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(imageElement, 0, 0, this.hashSize, this.hashSize);

      const imageData = ctx.getImageData(0, 0, this.hashSize, this.hashSize);
      const pixels = imageData.data;

      const colorPixels = {
        r: new Array(this.hashSize * this.hashSize),
        g: new Array(this.hashSize * this.hashSize),
        b: new Array(this.hashSize * this.hashSize),
      };

      for (let i = 0; i < pixels.length; i += 4) {
        const index = i / 4;
        colorPixels.r[index] = pixels[i];
        colorPixels.g[index] = pixels[i + 1];
        colorPixels.b[index] = pixels[i + 2];
      }

      const dctR = this.computeDCT(colorPixels.r);
      const dctG = this.computeDCT(colorPixels.g);
      const dctB = this.computeDCT(colorPixels.b);

      const freqSize = Math.floor(this.hashSize / 4);
      const averages = this.calculateChannelAverages(dctR, dctG, dctB, freqSize);

      let hash = '';
      for (let y = 0; y < freqSize; y++) {
        for (let x = 0; x < freqSize; x++) {
          if (!(x === 0 && y === 0)) {
            const pos = y * this.hashSize + x;
            hash += dctR[pos] > averages.r ? '1' : '0';
            hash += dctG[pos] > averages.g ? '1' : '0';
            hash += dctB[pos] > averages.b ? '1' : '0';
          }
        }
      }

      resolve(hash);
    });
  }

  calculateChannelAverages(dctR, dctG, dctB, freqSize) {
    let sumR = 0, sumG = 0, sumB = 0, count = 0;

    for (let y = 0; y < freqSize; y++) {
      for (let x = 0; x < freqSize; x++) {
        if (!(x === 0 && y === 0)) {
          const pos = y * this.hashSize + x;
          sumR += dctR[pos];
          sumG += dctG[pos];
          sumB += dctB[pos];
          count++;
        }
      }
    }

    return {
      r: sumR / count,
      g: sumG / count,
      b: sumB / count,
    };
  }

  /**
   * Cosine table for the DCT-II basis, built once per hash size.
   * cos[k * n + i] = cos((2i + 1) * k * PI / (2n))
   */
  cosTable(n) {
    if (!this._cos || this._cos.n !== n) {
      const table = new Float64Array(n * n);
      for (let k = 0; k < n; k++) {
        for (let i = 0; i < n; i++) {
          table[k * n + i] = Math.cos(((2 * i + 1) * k * Math.PI) / (2 * n));
        }
      }
      this._cos = { n, table };
    }
    return this._cos.table;
  }

  /**
   * DCT-II of one 24x24 plane, computed only where the hash reads it.
   *
   * The hash keeps the freqSize x freqSize lowest coefficients, so 540 of the
   * 576 are discarded. The old version computed all of them with the naive
   * O(n^4) sum — 331,776 iterations and two Math.cos calls each, per colour
   * channel, per frame. Separating the transform into rows then columns and
   * stopping at the frequencies the hash needs is the same maths in ~4.3k
   * multiplies with no trigonometry inside the loops.
   *
   * Returns an n*n array with only the [0, freq)^2 block filled, which is all
   * calculateChannelAverages() and calculateHash() read.
   */
  computeDCT(pixels) {
    const n = this.hashSize;
    const freq = Math.floor(n / 4);
    const cos = this.cosTable(n);
    const result = new Float64Array(n * n);
    const rows = new Float64Array(freq * n);

    for (let u = 0; u < freq; u++) {
      const cu = u === 0 ? Math.SQRT1_2 : 1;
      const off = u * n;
      for (let y = 0; y < n; y++) {
        const row = y * n;
        let sum = 0;
        for (let x = 0; x < n; x++) sum += pixels[row + x] * cos[off + x];
        rows[off + y] = sum * cu;
      }
    }

    for (let v = 0; v < freq; v++) {
      const cv = v === 0 ? Math.SQRT1_2 : 1;
      const coff = v * n;
      const scale = (2 * cv) / n;
      for (let u = 0; u < freq; u++) {
        const off = u * n;
        let sum = 0;
        for (let y = 0; y < n; y++) sum += rows[off + y] * cos[coff + y];
        result[off + v] = scale * sum;
      }
    }

    return result;
  }

  /**
   * Match an image against the loaded index
   * @param {HTMLImageElement|HTMLCanvasElement|HTMLVideoElement} imageElement
   * @param {number} threshold - Similarity threshold (0-100), default 85
   * @returns {Array} Top matches sorted by similarity desc: [{ id, name, set, number, rarity, image, similarity, hashDistance }]
   */
  async match(imageElement, threshold = 85, topK = 5) {
    if (!this.indexLoaded) {
      console.warn('[PerceptualHash] Index not loaded');
      return [];
    }

    const queryHash = await this.calculateHash(imageElement);
    const packed = this._packed;
    if (!packed) return [];

    const queryLen = queryHash.length || 1;
    const words = this._words;
    const count = this._packedIds.length;
    const qw = new Uint32Array(words);
    const bits = Math.min(queryHash.length, words * 32);
    for (let b = 0; b < bits; b++) {
      if (queryHash.charCodeAt(b) === 49) qw[b >> 5] |= 1 << (b & 31);
    }

    // Bounded top-K: one pass keeps the K best distances, so no 40k-entry
    // result array is built and no 40k-entry sort runs per frame.
    const k = Math.max(1, Math.min(topK, count));
    const bestDist = new Int32Array(k).fill(0x7fffffff);
    const bestRow = new Int32Array(k).fill(-1);
    const lens = this._packedLens;

    for (let i = 0, row = 0; i < count; i++, row += words) {
      let distance = queryLen - lens[i];
      if (distance < 0) distance = -distance;
      for (let w = 0; w < words; w++) {
        let v = packed[row + w] ^ qw[w];
        v -= (v >>> 1) & 0x55555555;
        v = (v & 0x33333333) + ((v >>> 2) & 0x33333333);
        v = (v + (v >>> 4)) & 0x0f0f0f0f;
        distance += (v * 0x01010101) >>> 24;
      }
      if (distance >= bestDist[k - 1]) continue;
      let j = k - 1;
      while (j > 0 && bestDist[j - 1] > distance) {
        bestDist[j] = bestDist[j - 1];
        bestRow[j] = bestRow[j - 1];
        j--;
      }
      bestDist[j] = distance;
      bestRow[j] = i;
    }

    const results = [];
    for (let j = 0; j < k; j++) {
      const i = bestRow[j];
      if (i < 0) continue;
      const similarity = 100 - (bestDist[j] / queryLen) * 100;
      // Distances rise down the list, so the first miss ends the run.
      if (similarity < threshold) break;
      const id = this._packedIds[i];
      const card = this.cardHashes.get(id);
      if (!card) continue;
      results.push({
        id,
        name: card.name,
        set: card.set,
        number: card.number,
        rarity: card.rarity,
        image: card.image,
        similarity: Math.round(similarity * 100) / 100,
        hashDistance: bestDist[j],
      });
    }

    // Hashing the crop is the expensive part of a match and the multi-frame
    // burst needs it again to compare frames; hand it back rather than making
    // the caller re-hash the same canvas.
    results.queryHash = queryHash;
    return results;
  }

  /**
   * Precompute hashes for all card images (run once during index build)
   * @param {Array} cards - Array of { id, name, set, number, rarity, image }
   * @returns {Array} Cards with hash field added
   */
  async precomputeHashes(cards) {
    const results = [];
    for (const card of cards) {
      try {
        const img = await this.loadImage(card.image);
        const hash = await this.calculateHash(img);
        results.push({ ...card, hash });
      } catch (e) {
        console.warn(`[PerceptualHash] Failed to hash ${card.id}:`, e);
        results.push({ ...card, hash: null });
      }
    }
    return results;
  }

  loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'Anonymous';
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`Failed to load ${src}`));
      img.src = src;
    });
  }
}

export const hashMatcher = new PerceptualHashMatcher();
window.PerceptualHashMatcher = PerceptualHashMatcher;
