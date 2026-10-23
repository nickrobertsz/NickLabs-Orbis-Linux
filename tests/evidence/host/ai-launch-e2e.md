# Host end-to-end: AI launcher → llama-server → orbis-web chat (2026-10-23)

Environment: x86-64 build host (2 vCPU), fixture PS4 root (tools/dev/demo_stack.py), llama.cpp b11845
CPU build with the Orbis Jaguar flags, tiny random-weight model from make_tiny_model.py. NOT a PS4.

- orbis-ai-launch computed: backend=cpu ("CPU is the default engine"), ctx=4096, batch 512/256,
  threads=2, budget {mem_available 4882 MiB (fixture), weights 3, kv 4, compute 3, headroom 512},
  then exec()ed llama-server with `--device none`.
- GET /health → {"status": "ok"}
- POST /api/v1/ai/chat through orbis-web (token login + CSRF) → completion returned
  (gibberish by design: random weights); 169 tok/s generation on the host.
- GET /api/v1/ai/status reported active backend "cpu" from /run/orbis-ai/llama-launch.json.
