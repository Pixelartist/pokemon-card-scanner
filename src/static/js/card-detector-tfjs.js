import * as tf from '@tensorflow/tfjs';

export class PokemonCardDetector {
  constructor() {
    this.model = null;
    this.loading = null;
    this.numClass = 1;
  }

  async loadModel(modelPath = '/model/model.json') {
    if (this.model) return;
    if (this.loading) return this.loading;

    this.loading = tf.loadGraphModel(modelPath)
      .then((loadedModel) => {
        this.model = loadedModel;
        console.log('[TF.js] YOLO11 OBB model loaded successfully');
      })
      .catch((error) => {
        console.error('[TF.js] Error loading model:', error);
        this.loading = null;
        throw error;
      });

    return this.loading;
  }

  isModelLoaded() {
    return !!this.model;
  }

  preprocessImage(imageElement, modelWidth = 640, modelHeight = 640) {
    const originalWidth = imageElement.width;
    const originalHeight = imageElement.height;
    const maxSize = Math.max(originalWidth, originalHeight);
    const paddedWidth = maxSize;
    const paddedHeight = maxSize;

    const input = tf.tidy(() => {
      const img = tf.browser.fromPixels(imageElement);
      const imgPadded = img.pad([
        [0, maxSize - originalHeight],
        [0, maxSize - originalWidth],
        [0, 0],
      ]);
      return tf.image
        .resizeBilinear(imgPadded as tf.Tensor3D, [modelWidth, modelHeight])
        .div(255.0)
        .expandDims(0);
    });

    return {
      input,
      originalWidth,
      originalHeight,
      paddedWidth,
      paddedHeight,
    };
  }

  async detect(imageElement) {
    if (!this.model) {
      await this.loadModel();
    }

    const modelWidth = 640;
    const modelHeight = 640;
    const scoreThreshold = 0.1;
    const iouThreshold = 0.1;

    const { input, originalWidth, originalHeight, paddedWidth, paddedHeight } = this.preprocessImage(imageElement, modelWidth, modelHeight);

    try {
      tf.engine().startScope();

      const predictions = this.model.predict(input);

      const [boxes, scores, classes, angles, boxesTransposed] = tf.tidy(() => {
        let transRes;

        if (predictions instanceof tf.Tensor && predictions.shape.length === 3 && predictions.shape[0] === 1) {
          transRes = predictions.squeeze([0]);
        } else {
          transRes = predictions;
        }

        const boxesSlice = transRes.slice([0, 0], [4, -1]);
        const boxesTransposed = boxesSlice.transpose();
        const x = boxesTransposed.slice([0, 0], [-1, 1]);
        const y = boxesTransposed.slice([0, 1], [-1, 1]);
        const w = boxesTransposed.slice([0, 2], [-1, 1]);
        const h = boxesTransposed.slice([0, 3], [-1, 1]);
        const x1 = tf.sub(x, tf.div(w, 2));
        const y1 = tf.sub(y, tf.div(h, 2));
        const x2 = tf.add(x1, w);
        const y2 = tf.add(y1, h);
        const boxes = tf.concat([y1, x1, y2, x2], 1);

        const classScores = transRes.slice([4, 0], [this.numClass, -1]);
        const scores = tf.max(classScores, 0);
        const classes = tf.argMax(classScores, 0);

        const numDimensions = transRes.shape[0];
        let angles = tf.zerosLike(scores);
        if (numDimensions !== undefined && numDimensions > 4 + this.numClass) {
          angles = transRes.slice([4 + this.numClass, 0], [1, -1]).squeeze();
        }

        return [boxes, scores, classes, angles, boxesTransposed];
      });

      const nms = await tf.image.nonMaxSuppressionAsync(boxes, scores, 100, iouThreshold, scoreThreshold);

      const detections = tf.tidy(() => tf.concat([
        boxesTransposed.gather(nms, 0),
        scores.gather(nms, 0).expandDims(1),
        classes.gather(nms, 0).expandDims(1),
        angles.gather(nms, 0).expandDims(1)
      ], 1));

      const detData = detections.dataSync();
      const numDetections = detections.shape[0];
      const boundingBoxes = [];

      const scaleX = originalWidth / paddedWidth;
      const scaleY = originalHeight / paddedHeight;

      for (let i = 0; i < numDetections; i++) {
        const offset = i * 7;
        const x = detData[offset];
        const y = detData[offset + 1];
        const w = detData[offset + 2];
        const h = detData[offset + 3];
        const score = detData[offset + 4];
        const label = detData[offset + 5];
        const angle = detData[offset + 6];

        const origX = (x * originalWidth) / 640 / scaleX;
        const origY = (y * originalHeight) / 640 / scaleY;
        const origW = (w * originalWidth) / 640 / scaleX;
        const origH = (h * originalHeight) / 640 / scaleY;

        const cosA = Math.cos(angle);
        const sinA = Math.sin(angle);

        const hw = origW / 2;
        const hh = origH / 2;

        const corners = [
          [-hw, -hh],
          [hw, -hh],
          [hw, hh],
          [-hw, hh]
        ];

        const points = corners.map(([cx, cy]) => {
          const rx = cx * cosA - cy * sinA;
          const ry = cx * sinA + cy * cosA;
          return [origX + rx, origY + ry];
        });

        boundingBoxes.push({
          points,
          confidence: score * 100,
          class: 'pokemon_card',
          label: label,
          angle: angle,
          centerX: origX,
          centerY: origY,
          width: origW,
          height: origH,
        });
      }

      return boundingBoxes;
    } finally {
      input.dispose();
      tf.engine().endScope();
    }
  }
}

export const detector = new PokemonCardDetector();