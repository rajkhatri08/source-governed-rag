import chromadb


def build_index(chunks, metadatas):
    client = chromadb.Client()
    try:
        client.delete_collection("gdpr")
    except:
        pass
    collection = client.create_collection("gdpr")

    ids = []
    for i in range(len(chunks)):
        ids.append("chunk_" + str(i))

    collection.add(documents=chunks, metadatas=metadatas, ids=ids)
    return collection


def search(collection, question, n):
    results = collection.query(query_texts=[question], n_results=n)
    return results["documents"][0], results["ids"][0], results["distances"][0], results["metadatas"][0]