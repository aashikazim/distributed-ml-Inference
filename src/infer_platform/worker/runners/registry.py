"""Backend name -> runner factory. Adding a model family means adding one entry here.

Factories import heavy libraries lazily, so a fake-only image never imports torch,
transformers or onnxruntime. Only names listed here can be configured; there is no
dynamic import of user-supplied module paths.
"""

from collections.abc import Callable
from pathlib import Path

from infer_platform.common.settings import ModelConfig, Settings
from infer_platform.worker.runners.base import ModelRunner

RunnerFactory = Callable[[ModelConfig, Settings], ModelRunner]
_FACTORIES: dict[str, RunnerFactory] = {}


def register(backend: str) -> Callable[[RunnerFactory], RunnerFactory]:
    def decorate(factory: RunnerFactory) -> RunnerFactory:
        if backend in _FACTORIES:
            raise ValueError(f"runner backend '{backend}' registered twice")
        _FACTORIES[backend] = factory
        return factory

    return decorate


def backends() -> list[str]:
    return sorted(_FACTORIES)


def create_runner(config: ModelConfig, settings: Settings) -> ModelRunner:
    factory = _FACTORIES.get(config.backend)
    if factory is None:
        raise ValueError(f"backend '{config.backend}' has no ModelRunner (LLMs use an adapter)")
    return factory(config, settings)


@register("fake")
def _fake(config: ModelConfig, settings: Settings) -> ModelRunner:
    from infer_platform.worker.runners.fake import FakeRunner

    return FakeRunner(config.fake)


@register("torch")
def _torch(config: ModelConfig, settings: Settings) -> ModelRunner:
    from infer_platform.worker.runners.torch_classifier import TorchClassifierRunner

    assert config.hf_id is not None
    return TorchClassifierRunner(
        config.hf_id,
        config.revision,
        settings.device,
        settings.torch_threads,
        settings.torch_fp16,
        settings.model_cache,
        settings.model_local_files_only,
    )


@register("hf_pipeline")
def _hf_pipeline(config: ModelConfig, settings: Settings) -> ModelRunner:
    from infer_platform.worker.runners.hf_pipeline import HFPipelineRunner

    assert config.hf_id is not None and config.pipeline is not None
    return HFPipelineRunner(
        task=config.pipeline.task,
        hf_id=config.hf_id,
        revision=config.revision,
        inputs=config.input_specs(),
        kwargs=config.pipeline.kwargs,
        device=settings.device,
        threads=settings.torch_threads,
        cache_dir=settings.model_cache,
        local_files_only=settings.model_local_files_only,
    )


@register("onnx")
def _onnx(config: ModelConfig, settings: Settings) -> ModelRunner:
    from infer_platform.worker.runners.onnx import OnnxRunner

    assert config.onnx is not None
    path = config.onnx.path
    if not path.is_absolute():
        # Relative paths resolve against the model cache, never the caller's input.
        path = Path(settings.model_cache) / path
    return OnnxRunner(
        path,
        inputs=config.input_specs(),
        outputs=config.onnx.outputs,
        threads=config.onnx.intra_op_threads,
    )
