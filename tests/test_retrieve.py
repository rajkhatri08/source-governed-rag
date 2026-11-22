import retrieve


class Vector(list):
    """Stands in for the numpy array the model returns for one text."""

    def tolist(self) -> list:
        return list(self)


def test_embed_sends_the_model_at_most_embed_batch_texts_per_call_and_keeps_their_order(monkeypatch):
    calls = []

    def model(texts):
        calls.append(len(texts))
        return [Vector([float(len(text))]) for text in texts]

    monkeypatch.setattr(retrieve, "EMBEDDER", model)
    monkeypatch.setattr(retrieve, "EMBED_BATCH", 2)
    assert retrieve.embed(["a", "bb", "ccc", "dddd", "eeeee"]) == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert calls == [2, 2, 1]


def test_the_index_measures_l2_distance_with_the_shared_model():
    collection = retrieve.new_index()  # an empty in-memory collection: nothing is embedded or downloaded
    assert collection.configuration["hnsw"]["space"] == "l2"
    assert collection._embedding_function is retrieve.EMBEDDER
