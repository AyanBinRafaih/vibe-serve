# NxD Inference (NeuronX Distributed Inference)

Short pointer, by design. **NxD Inference** is AWS's turnkey inference library
in the Neuron SDK: you give it a supported model + a config and it handles
distribution, compilation, and serving on Trainium/Inferentia.

## What it gives you

- Pre-built modeling for popular architectures (Llama, Mixtral, …) with
  **minimal config**.
- Tensor + sequence **parallelism** and **weight sharding** across NeuronCores.
- **Continuous batching**, **KV cache**, flash-attention, **on-device sampling**,
  quantization — all wired up.
- Core API surface: `NeuronConfig`, `ModelBuilder`, `generate`.

It is built on `torch-neuronx` (it compiles via `neuronx-cc` like everything
else — see [`neuron-pytorch.md`](neuron-pytorch.md)).

## When to use it — and when not to

- **Use it** when the goal is "serve model X on Trainium" with the least code and
  you're allowed turnkey libraries.
- **Do NOT import it** when the task is to build a **bespoke / from-scratch**
  serving system (it *is* the turnkey path you're asked to replace). In that
  case, treat NxD as a **reference for the techniques** — how it shards, batches,
  caches KV, and samples on-device — and reimplement what you need over your own
  static-shape graphs.

For the actual implementation patterns (static shapes, compile cache, BF16,
KV-cache buffers) read [`neuron-pytorch.md`](neuron-pytorch.md); for custom
kernels, the `neuron-nki-*` skills.

Source: [NxD Inference docs](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/libraries/nxd-inference/index.html).
