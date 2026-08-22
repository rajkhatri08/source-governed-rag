import chromadb
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")

def build_index(chunks, doc_meta):
    client = chromadb.Client()
    try:
        client.delete_collection("gdpr")
    except:
        pass
    collection = client.create_collection("gdpr")

    vectors = model.encode(chunks).tolist()

    ids = []
    metadatas = []
    for i in range(len(chunks)):
        ids.append("chunk_" + str(i))
        metadatas.append(doc_meta)

    collection.add(documents=chunks, embeddings=vectors, ids=ids, metadatas=metadatas)
    return collection


def search(collection, question, n):
    query_vector = model.encode(question).tolist()
    results = collection.query(query_embeddings=[query_vector], n_results=n)
    return results["documents"][0], results["ids"][0], results["distances"][0], results["metadatas"][0]
