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
  }

  /**
   * Load the card index with precomputed hashes
   * @param {Array} cards - Array of { id, name, set, number, rarity, image, hash }
   */
  loadIndex(cards) {
    this.cardHashes.clear();
    for (const card of cards) {
      if (card.hash) {
        this.cardHashes.set(card.id, {
          hash: card.hash,
          name: card.name,
          set: card.set,
          number: card.number,
          rarity: card.rarity,
          image: card.image,
        });
      }
    }
    this.indexLoaded = true;
    console.log(`[PerceptualHash] Loaded ${this.cardHashes.size} card hashes`);
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

  computeDCT(pixels) {
    const n = this.hashSize;
    const result = new Array(n * n);

    for (let u = 0; u < n; u++) {
      for (let v = 0; v < n; v++) {
        let sum = 0;
        for (let y = 0; y < n; y++) {
          for (let x = 0; x < n; x++) {
            sum += pixels[y * n + x] *
              Math.cos(((2 * x + 1) * u * Math.PI) / (2 * n)) *
              Math.cos(((2 * y + 1) * v * Math.PI) / (2 * n));
          }
        }

        const cu = u === 0 ? 1 / Math.sqrt(2) : 1;
        const cv = v === 0 ? 1 / Math.sqrt(2) : 1;

        result[u * n + v] = ((2 * cu * cv) / n) * sum;
      }
    }

    return result;
  }

  calculateHammingDistance(hash1, hash2) {
    let distance = 0;
    const minLength = Math.min(hash1.length, hash2.length);

    for (let i = 0; i < minLength; i++) {
      if (hash1[i] !== hash2[i]) distance++;
    }

    distance += Math.abs(hash1.length - hash2.length);
    return distance;
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
    const results = [];

    for (const [id, card] of this.cardHashes) {
      const distance = this.calculateHammingDistance(queryHash, card.hash);
      const maxDistance = queryHash.length;
      const similarity = 100 - (distance / maxDistance) * 100;

      if (similarity >= threshold) {
        results.push({
          id,
          name: card.name,
          set: card.set,
          number: card.number,
          rarity: card.rarity,
          image: card.image,
          similarity: Math.round(similarity * 100) / 100,
          hashDistance: distance,
        });
      }
    }

    results.sort((a, b) => b.similarity - a.similarity);
    return results.slice(0, topK);
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