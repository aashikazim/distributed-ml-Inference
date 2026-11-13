# ADR-0012: Runner registry and generic backends

## Status
Accepted in Phase D (generic serving).

## Context
`make_runner` was an if/else over `backend` in the worker's entry point, and the
only real runner was a DistilBERT classifier. Serving another model family meant
editing core worker code. "Generic" must not mean "load whatever the request names":
the model allowlist and no-remote-code rules from AGENTS.md §12 still apply.

## Decision
`worker/runners/registry.py` maps a backend name to a factory. Factories import heavy
libraries lazily, so the fake-only image never imports torch or onnxruntime. The set
of names is closed: there is no dynamic import of module paths from configuration.
Backends now:

| Backend | Covers | Notes |
|---|---|---|
| `fake` | simulated cost | unchanged |
| `torch` | the original DistilBERT-style sequence classifier | unchanged; gains `cost()` |
| `hf_pipeline` | text classification, token classification, embeddings (mean-pooled), image classification | allowlisted `pipeline.task`; safetensors only; components loaded explicitly to honour cache dir and offline mode |
| `onnx` | any model exported to ONNX with tensor inputs (PyTorch, TF, scikit-learn via converters) | `onnx.path` from trusted config only; relative paths resolve under the model cache; CPU provider |
| `llm` | chat via vLLM | still an adapter, not a ModelRunner (ADR-0007) |

Runners may implement an optional `cost(body)` (see ADR-0013). The `ModelRunner`
protocol itself is unchanged: `load`, `warmup`, `predict_batch`.

## Alternatives considered
- **Python entry points / import-path plugins:** truly open-ended, but lets
  configuration load arbitrary code, which conflicts with the security rules. Easy to
  add later behind an explicit allowlist.
- **NVIDIA Triton or KServe as the execution layer:** mature multi-framework
  serving, with dynamic batching and ensembles built in. It's the right answer for many
  production fleets, and the natural successor to `onnx` here. Rejected for this
  learning project because it would hide the batching and runner code the lessons teach,
  and add a heavy dependency.
- **One runner class per model:** most control, but every new model costs code. The
  `hf_pipeline` runner covers thousands of Hub models with configuration only.

## Consequences
- Adding a model family = one runner module + one `@register` entry + tests.
- `hf_pipeline` trades some performance for coverage. The pipeline does its own
  padding and pre/post-processing, so the bespoke `torch` runner stays for the
  measured sentiment path.
- `onnx` stacks per-item tensors along a new batch axis. Items whose shapes differ
  cannot share a tensor, so the batch call fails and the batcher's per-item retry runs
  them alone. That's correct but slow for ragged inputs; padding or bucketing is future work.
- ONNX Runtime GPU (CUDA/TensorRT providers) is not configured. The `cuda` image
  installs the CPU `onnxruntime` package.
