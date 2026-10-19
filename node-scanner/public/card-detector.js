/**
 * Lightweight card detector in pure JavaScript.
 * Port of the numpy fallback from card_detector.py.
 * Detects card by: grayscale -> blur -> Canny edges -> contours -> aspect ratio filter.
 * Returns {x, y, w, h} in original image coordinates, or null.
 */
async function detectCard(blob) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0);
      
      // Grayscale + blur + Canny (approximated via simple edge detection)
      const src = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const gray = new Uint8Array(src.width * src.height);
      for (let i = 0; i < gray.length; i++) {
        const r = src.data[i * 4];
        const g = src.data[i * 4 + 1];
        const b = src.data[i * 4 + 2];
        gray[i] = (r * 76 + g * 150 + b * 30) >> 8; // 0.299R + 0.587G + 0.114B
      }
      
      // Simple 3x3 box blur
      const blur = new Float32Array(gray.length);
      for (let y = 1; y < src.height - 1; y++) {
        for (let x = 1; x < src.width - 1; x++) {
          let sum = 0;
          for (let dy = -1; dy <= 1; dy++) {
            for (let dx = -1; dx <= 1; dx++) {
              sum += gray[(y + dy) * src.width + (x + dx)];
            }
          }
          blur[y * src.width + x] = sum / 9.0;
        }
      }
      
      // Sobel edges (approximate Canny)
      const edge = new Float32Array(gray.length);
      for (let y = 1; y < src.height - 1; y++) {
        for (let x = 1; x < src.width - 1; x++) {
          const gx = -blur[(y-1)*src.width+(x-1)] + blur[(y-1)*src.width+(x+1)]
                   - 2*blur[y*src.width+(x-1)]     + 2*blur[y*src.width+(x+1)]
                   - blur[(y+1)*src.width+(x-1)]   + blur[(y+1)*src.width+(x+1)];
          const gy = -blur[(y-1)*src.width+(x-1)] - 2*blur[(y-1)*src.width+x] - blur[(y-1)*src.width+(x+1)]
                   + blur[(y+1)*src.width+(x-1)] + 2*blur[(y+1)*src.width+x] + blur[(y+1)*src.width+(x+1)];
          edge[y*src.width+x] = Math.sqrt(gx*gx + gy*gy);
        }
      }
      
      // Threshold + find contours (simple connected components on edges)
      const threshold = 50; // sobel magnitude threshold
      const binary = new Uint8Array(gray.length);
      for (let i = 0; i < binary.length; i++) binary[i] = edge[i] > threshold ? 255 : 0;
      
      // Flood-fill to find contours (simplified: just look for large rectangular regions)
      const visited = new Uint8Array(gray.length);
      const minArea = src.width * src.height * 0.01; // 1% of image
      const targetRatio = 63/88; // ~0.716
      const ratioTol = 0.15;
      let best = null, bestArea = 0;
      
      for (let y = 0; y < src.height; y++) {
        for (let x = 0; x < src.width; x++) {
          const idx = y * src.width + x;
          if (binary[idx] && !visited[idx]) {
            // BFS flood fill
            const stack = [idx];
            visited[idx] = 1;
            let minX = x, maxX = x, minY = y, maxY = y, area = 0;
            while (stack.length) {
              const cur = stack.pop();
              const cy = Math.floor(cur / src.width);
              const cx = cur % src.width;
              area++;
              if (cx < minX) minX = cx;
              if (cx > maxX) maxX = cx;
              if (cy < minY) minY = cy;
              if (cy > maxY) maxY = cy;
              // 4-connected neighbors
              const nbs = [cur-src.width, cur+src.width, cur-1, cur+1];
              for (const nb of nbs) {
                if (nb >= 0 && nb < binary.length && binary[nb] && !visited[nb]) {
                  visited[nb] = 1;
                  stack.push(nb);
                }
              }
            }
            if (area > minArea) {
              const w = maxX - minX + 1;
              const h = maxY - minY + 1;
              const ratio = w / h;
              if (Math.abs(ratio - targetRatio) < ratioTol && area > bestArea) {
                bestArea = area;
                best = {x: minX, y: minY, w, h};
              }
            }
          }
        }
      }
      
      resolve(best);
    };
    img.src = URL.createObjectURL(blob);
  });
}

window.detectCard = detectCard;