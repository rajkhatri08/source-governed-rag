"""Small files built in memory for the upload tests, so no fixture file is needed."""
import io

from pypdf import PdfReader, PdfWriter


def blank_pdf(pages: int = 1) -> bytes:
    """A PDF whose pages have no text layer, as a scanned document's don't."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def pages_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with one page per list, each page's text layer holding its lines (ASCII, no parentheses or backslashes).

    Written out object by object, because pypdf has no public way to put text on a page.
    """
    page_numbers = [4 + 2 * i for i in range(len(pages))]  # objects 1-3 are below; each page is followed by its content
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_numbers)}] /Count {len(pages)} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for number, lines in zip(page_numbers, pages):
        content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {number + 1} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    pdf += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    return pdf


def text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds `lines`."""
    return pages_pdf([lines])


def encrypted_pdf(user_password: str, owner_password: str | None = None) -> bytes:
    """A one-page text PDF encrypted with AES-128.

    With an empty user password it opens without one, as PDFs locked only against printing or copying do.
    """
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf(["Section 1. Scope", "Within 72 hours."]))))
    writer.encrypt(user_password, owner_password, algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def numbered_sections(count: int) -> bytes:
    """A .txt file that the chunker splits into exactly `count` chunks, one per "Section N." line."""
    return "\n".join(f"Section {n}. Keep records." for n in range(1, count + 1)).encode()
