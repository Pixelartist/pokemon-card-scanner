#!/usr/bin/env python3
"""Diagnose local vs cloud scanner divergence."""
import json
import numpy as np

emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
ids = [str(i) for i in emb['card_ids']]
mat = emb['embeddings'].astype(np.float32)
idx = np.fromfile('/opt/data/pokemon-card-scanner/.worktrees/local-scanner/node-scanner/public/card-index.bin', dtype=np.int8).reshape(38420, 512)

print('cloud shape:', mat.shape, 'dtype', mat.dtype)
print('browser shape:', idx.shape, 'dtype', idx.dtype)
print('ids match:', list(ids) == json.load(open('/opt/data/pokemon-card-scanner/.worktrees/local-scanner/node-scanner/public/card-ids.json')))
q = np.round(mat * 127).astype(np.int8)
print('quant match:', np.array_equal(q, idx), 'max diff:', np.abs(q.astype(int) - idx.astype(int)).max())