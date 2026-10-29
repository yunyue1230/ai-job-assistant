from io import BytesIO

from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}


def parse_resume_file(file_name: str, file_bytes: bytes) -> str:
    extension = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Please upload one of: {supported}")

    if extension == ".pdf":
        reader = PdfReader(BytesIO(file_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = file_bytes.decode("utf-8-sig")

    text = text.strip()
    if not text:
        raise ValueError("The uploaded resume does not contain readable text.")

    return text
