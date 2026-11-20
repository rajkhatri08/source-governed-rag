import hashlib

import chromadb
from chromadb.errors import NotFoundError


def content_hash(text: str) -> str:
    """The SHA-256 of a text, as 64 hex characters. It changes whenever the text does."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(filename: str, index: int, text: str) -> str:
    """A chunk's id, from what the chunk is rather than when it was loaded: the same chunk gets the same id on every start."""
    return f"{filename}::{index}::{content_hash(text)[:12]}"


def new_index():
    """A new, empty collection. One left from earlier in this process (an uvicorn reload) is dropped first."""
    client = chromadb.Client()
    try:
        client.delete_collection("gdpr")
    except NotFoundError:
        pass
    return client.create_collection("gdpr")


def search(collection, question, n):
    results = collection.query(query_texts=[question], n_results=n)
    return results["documents"][0], results["ids"][0], results["distances"][0], results["metadatas"][0]


def add_to_index(collection, ids, chunks, metadatas):
    collection.add(ids=ids, documents=chunks, metadatas=metadatas)
    return len(chunks)
