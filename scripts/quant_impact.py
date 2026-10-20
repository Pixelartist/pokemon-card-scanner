#!/usr/bin/env python3
"""Quantify ranking impact of int8 quantization on the card index.

Simulates the browser matcher: query (normalized, quantized to int8) vs
int8 index, compared to full float32 cosine ranking.
"""
import numpy as np

emb = np.load('/opt/data/pokemon-card-scanner/data/clip_embeddings.npz', allow_pickle=True)
ids = [str(i) for i in emb['card_ids']]
mat = emb['embeddings'].astype(np.float32)
mat /= np.clip(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9, None)

# int8 index as shipped to browser
q_idx = np.clip(np.rint(mat * 127.0), -127, 127).astype(np.int8).astype(np.float32)

rng = np.random.default_rng(42)
sample = rng.choice(len(ids), 200, replace=False)

top1_agree = 0
top5_overlap = 0
sim_err = []
for i in sample:
    q = mat[i]
    # float path (cloud-equivalent)
    sims_f = mat @ q
    # int8 path (browser-equivalent): browser does dot/(127*127) on quantised query
    qq = np.clip(np.rint(q * 127.0), -127, 127) * 1.0
    sims_q = (q_idx @ qq) / (127.0 * 127.0)

    order_f = np.argsort(-sims_f)
    order_q = np.argsort(-sims_q)
    if order_f[0] == order_q[0]:
        top1_agree += 1
    top5_overlap += len(set(order_f[:5].tolist()) & set(order_q[:5].tolist()))
    sim_err.append(np.abs(sims_f - sims_q).max())

print(f'queries: {len(sample)}')
print(f'top-1 identical: {top1_agree}/{len(sample)} ({100*top1_agree/len(sample):.0f}%)')
print(f'avg top-5 overlap: {top5_overlap/len(sample):.2f}/5')
print(f'max sim error: mean {np.mean(sim_err):.4f}')