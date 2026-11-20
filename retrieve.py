import hashlib

import chromadb


def content_hash(text: str) -> str:
    """The SHA-256 of a chunk's text, as 64 hex characters. It changes whenever the text does."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(filename: str, index: int, text: str) -> str:
    """A chunk's id, from what the chunk is rather than when it was loaded: the same chunk gets the same id on every start."""
    return f"{filename}::{index}::{content_hash(text)[:12]}"


def build_index(ids, chunks, metadatas):
    client = chromadb.Client()
    try:
        client.delete_collection("gdpr")
    except:
        pass
    collection = client.create_collection("gdpr")
    collection.add(ids=ids, documents=chunks, metadatas=metadatas)
    return collection


def search(collection, question, n):
    results = collection.query(query_texts=[question], n_results=n)
    return results["documents"][0], results["ids"][0], results["distances"][0], results["metadatas"][0]


def add_to_index(collection, ids, chunks, metadatas):
    collection.add(ids=ids, documents=chunks, metadatas=metadatas)
    return len(chunks)
