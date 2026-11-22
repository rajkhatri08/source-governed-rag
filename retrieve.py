import hashlib

import chromadb
from chromadb.errors import NotFoundError
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

# The one embedding model in this process, used for everything: the index embeds every question and any
# chunk added without a vector with it, and embed() computes the vectors that uploads store. It's Chroma's
# default model, all-MiniLM-L6-v2 run with ONNX, but kept as one instance: Chroma's DefaultEmbeddingFunction
# makes a new one, loading the model again (~170 MB), on every call. api.py's startup loads it once.
EMBEDDER = ONNXMiniLM_L6_V2()
# Stored with each vector. Change it whenever the embedding function changes (requirements.txt pins
# chromadb): startup then ignores the old vectors and embeds again, rather than mix two models' numbers.
EMBEDDING_MODEL = "chromadb default: all-MiniLM-L6-v2 (ONNX)"
# The index measures L2 distance, as it did with Chroma's default embedder, so every distance and the 1.2
# cutoff mean what they did. It must be pinned: given this model, Chroma would otherwise choose cosine.
DISTANCE = "l2"
# Texts per model call when embedding chunks. The model pads every text to 256 tokens, so a call's working
# memory grows with how many texts it holds, and stays reserved afterwards; the vectors are the same either
# way. Measured on 300 chunks (P0.3b): one text per call needed no memory beyond the loaded model and the
# least CPU; 32 per call (the embedder's default) peaked about 230 MB higher.
EMBED_BATCH = 1


def content_hash(text: str) -> str:
    """The SHA-256 of a text, as 64 hex characters. It changes whenever the text does."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(filename: str, index: int, text: str) -> str:
    """A chunk's id, from what the chunk is rather than when it was loaded: the same chunk gets the same id on every start."""
    return f"{filename}::{index}::{content_hash(text)[:12]}"


def new_index():
    """A new, empty collection that embeds with EMBEDDER and measures L2 distance.

    One left from earlier in this process (an uvicorn reload) is dropped first.
    """
    client = chromadb.Client()
    try:
        client.delete_collection("gdpr")
    except NotFoundError:
        pass
    return client.create_collection("gdpr", embedding_function=EMBEDDER, configuration={"hnsw": {"space": DISTANCE}})


def search(collection, question, n):
    results = collection.query(query_texts=[question], n_results=n)
    return results["documents"][0], results["ids"][0], results["distances"][0], results["metadatas"][0]


def embed(texts: list[str]) -> list[list[float]]:
    """Each text's vector, as plain floats, from the model the index embeds questions with.

    EMBED_BATCH texts per call to the model, so the memory needed doesn't grow with the number of texts.
    """
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(vector.tolist() for vector in EMBEDDER(texts[start:start + EMBED_BATCH]))
    return vectors


def add_to_index(collection, ids, chunks, metadatas, embeddings=None):
    """Add chunks to the index with their vectors. Given no vectors, Chroma computes them, which is the slow part."""
    collection.add(ids=ids, documents=chunks, metadatas=metadatas, embeddings=embeddings)
    return len(chunks)
