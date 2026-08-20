import chromadb
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")


def build_index(chunks):
    client = chromadb.Client()
    collection = client.get_or_create_collection("gdpr")

    vectors = model.encode(chunks).tolist()

    ids = []
    for i in range(len(chunks)):
        ids.append("chunk_" + str(i))

    collection.add(documents=chunks, embeddings=vectors, ids=ids)
    return collection


def search(collection, question, n):
    query_vector = model.encode(question).tolist()
    results = collection.query(query_embeddings=[query_vector], n_results=n)
    return results