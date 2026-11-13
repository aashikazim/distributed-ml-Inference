"""Write a minimal ONNX model (y = x + x) as raw protobuf, without the onnx package.

Field numbers follow onnx.proto (ModelProto, GraphProto, NodeProto, ValueInfoProto,
TypeProto, TensorShapeProto). Only what ONNX Runtime needs to load the graph is set.
"""

from pathlib import Path

FLOAT = 1


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _field(number: int, payload: bytes | str | int) -> bytes:
    if isinstance(payload, int):
        return _varint(number << 3) + _varint(payload)
    data = payload.encode() if isinstance(payload, str) else payload
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def _tensor_value(name: str, dims: list[int | str]) -> bytes:
    shape = b"".join(
        _field(1, _field(2, dim) if isinstance(dim, str) else _field(1, dim)) for dim in dims
    )
    tensor_type = _field(1, FLOAT) + _field(2, shape)
    return _field(1, name) + _field(2, _field(1, tensor_type))


def write_double_model(path: Path, width: int = 3) -> Path:
    """Input ``x`` [batch, width] float32 -> output ``y`` = x + x."""
    node = _field(1, "x") + _field(1, "x") + _field(2, "y") + _field(3, "double") + _field(4, "Add")
    graph = (
        _field(1, node)
        + _field(2, "double")
        + _field(11, _tensor_value("x", ["batch", width]))
        + _field(12, _tensor_value("y", ["batch", width]))
    )
    model = _field(1, 7) + _field(7, graph) + _field(8, _field(2, 13))
    path.write_bytes(model)
    return path
