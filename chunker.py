import re
with open("sample.txt", "r") as f:
    text = f.read()
def chunk_text(document,size, overlap):
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    chunks = []
    for churn in range(0,len(document),size-overlap):
        chunks.append(document[churn:churn+size])
    return chunks

def clean_text(document):
    document = re.sub(r"\[\d+\]", "", document)
    lines = document.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned)
cleaned_text = clean_text(text)
print(len(text))
print(len(cleaned_text))

small = chunk_text(cleaned_text, 300,50)
big = chunk_text(cleaned_text, 1000,50)

print(len(small))
print(len(big))
print(small[0][-50:])
print(small[1][:50])
print(cleaned_text[:500])
