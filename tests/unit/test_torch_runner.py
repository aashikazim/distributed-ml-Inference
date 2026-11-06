import pytest

pytestmark = pytest.mark.model


def test_padded_batch_matches_individual_predictions(sentiment_runner):
    texts = [
        "Wonderful!",
        "I found this film boring, slow and disappointing.",
        "The acting was superb and I enjoyed every moment. " * 80,
    ]
    alone = [sentiment_runner.predict_batch([{"input": text}])[0] for text in texts]
    batched = sentiment_runner.predict_batch([{"input": text} for text in texts])
    assert not sentiment_runner.model.training
    encoded = sentiment_runner.tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=256,
        return_tensors="pt",
    )
    assert encoded["input_ids"].shape == (3, 256)
    for single, batch in zip(alone, batched, strict=True):
        assert single["label"] == batch["label"]
        assert single["probabilities"] == pytest.approx(
            batch["probabilities"],
            rel=1e-5,
            abs=1e-5,
        )
    assert batched[0]["label"] == "POSITIVE"
    assert batched[1]["label"] == "NEGATIVE"
