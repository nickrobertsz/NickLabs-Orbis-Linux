#!/usr/bin/env python3
"""Generate a tiny, deterministic, random-weight llama-architecture GGUF.

Purpose: a self-contained fixture for the CPU-vs-Vulkan inference harness and
for CI. It needs no download and no license review, it exercises the same
llama.cpp graph (RMSNorm, RoPE, GQA attention, SwiGLU FFN, output head) as a
real model, and its output is gibberish by design: the harness compares
backends against each other, not against expected prose.

Requires numpy and the gguf-py package from the pinned llama.cpp checkout:
  make_tiny_model.py --gguf-py /path/to/llama.cpp/gguf-py -o tiny.gguf
"""
import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf-py", required=True, help="path to llama.cpp/gguf-py")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--n-embd", type=int, default=128)
    ap.add_argument("--n-layer", type=int, default=4)
    ap.add_argument("--n-head", type=int, default=4)
    ap.add_argument("--n-head-kv", type=int, default=2)
    ap.add_argument("--n-ff", type=int, default=352)
    ap.add_argument("--n-ctx", type=int, default=4096)
    a = ap.parse_args()
    sys.path.insert(0, a.gguf_py)
    import numpy as np
    import gguf

    rng = np.random.default_rng(a.seed)
    # Vocabulary: special tokens, 256 byte-fallback tokens, then printable pieces.
    tokens, scores, types = [], [], []
    for t in ("<unk>", "<s>", "</s>"):
        tokens.append(t); scores.append(0.0)
        types.append(gguf.TokenType.UNKNOWN if t == "<unk>" else gguf.TokenType.CONTROL)
    for b in range(256):
        tokens.append(f"<0x{b:02X}>"); scores.append(0.0); types.append(gguf.TokenType.BYTE)
    pieces = ["▁"] + [chr(c) for c in range(ord("a"), ord("z") + 1)] + \
             ["▁" + chr(c) for c in range(ord("a"), ord("z") + 1)] + \
             ["▁the", "▁and", "▁PS4", "▁Orbis", "▁Linux", "ing", "ed", "er"]
    for i, p in enumerate(pieces):
        tokens.append(p); scores.append(-float(i)); types.append(gguf.TokenType.NORMAL)
    n_vocab = len(tokens)

    w = gguf.GGUFWriter(a.output, "llama")
    w.add_name("orbis-tiny-random-llama")
    w.add_context_length(a.n_ctx)
    w.add_embedding_length(a.n_embd)
    w.add_block_count(a.n_layer)
    w.add_feed_forward_length(a.n_ff)
    w.add_head_count(a.n_head)
    w.add_head_count_kv(a.n_head_kv)
    w.add_layer_norm_rms_eps(1e-5)
    w.add_rope_dimension_count(a.n_embd // a.n_head)
    w.add_rope_freq_base(10000.0)
    w.add_file_type(gguf.LlamaFileType.ALL_F32)
    w.add_tokenizer_model("llama")
    w.add_token_list(tokens)
    w.add_token_scores(scores)
    w.add_token_types(types)
    w.add_bos_token_id(1)
    w.add_eos_token_id(2)
    w.add_unk_token_id(0)
    w.add_add_bos_token(True)
    w.add_add_eos_token(False)

    hd = a.n_embd // a.n_head

    def t(name, *shape, scale=0.08):
        w.add_tensor(name, (rng.standard_normal(shape) * scale).astype(np.float32))

    t("token_embd.weight", n_vocab, a.n_embd, scale=0.5)
    for i in range(a.n_layer):
        w.add_tensor(f"blk.{i}.attn_norm.weight", np.ones(a.n_embd, dtype=np.float32))
        t(f"blk.{i}.attn_q.weight", a.n_embd, a.n_embd)
        t(f"blk.{i}.attn_k.weight", a.n_head_kv * hd, a.n_embd)
        t(f"blk.{i}.attn_v.weight", a.n_head_kv * hd, a.n_embd)
        t(f"blk.{i}.attn_output.weight", a.n_embd, a.n_embd)
        w.add_tensor(f"blk.{i}.ffn_norm.weight", np.ones(a.n_embd, dtype=np.float32))
        t(f"blk.{i}.ffn_gate.weight", a.n_ff, a.n_embd)
        t(f"blk.{i}.ffn_up.weight", a.n_ff, a.n_embd)
        t(f"blk.{i}.ffn_down.weight", a.n_embd, a.n_ff)
    w.add_tensor("output_norm.weight", np.ones(a.n_embd, dtype=np.float32))
    t("output.weight", n_vocab, a.n_embd, scale=0.3)

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    print(f"wrote {a.output}: vocab={n_vocab} layers={a.n_layer} embd={a.n_embd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
