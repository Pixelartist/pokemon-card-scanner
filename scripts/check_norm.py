#!/usr/bin/env python3
"""Check normalization of cloud embeddings."""
import numpy as np

emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
mat = emb['embeddings'].astype(np.float32)

norms = np.linalg.norm(mat, axis=1)
print('cloud row norms: min=%.4f max=%.4f mean=%.4f' % (norms.min(), norms.max(), norms.mean()))

# check a specific card
ids = [str(i) for i in emb['card_ids']]
i = ids.index('gym2-1')
print('gym2-1 norm:', norms[i])
print('gym2-1 self-sim:', np.dot(mat[i], mat[i]) / (norms[i] * norms[i]))

# what the exporter does
emb_norm = mat / np.clip(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9, None)
q = np.clip(np.rint(emb_norm * 127.0), -127, 127).astype(np.int8)

# load browser index
idx = np.fromfile('/opt/data/pokemon-card-scanner/.worktrees/local-scanner/node-scanner/public/card-index.bin', dtype=np.int8).reshape(38420, 512)

print('exported gym2-1 norm:', np.linalg.norm(q[i]))
print('browser gym2-1 norm:', np.linalg.norm(idx[i]))

# check if cloud is ALREADY normalized
print('cloud already normalized?', np.allclose(norms, 1.0, atol=1e-3))