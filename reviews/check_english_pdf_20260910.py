"""Read generated PDFs, check page bounds, and produce visual contact sheets.

Requires PyMuPDF and Pillow; neither is added to project dependencies.
"""
from pathlib import Path
import json
import pymupdf as fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper-build-20260910"
reports = []
for stem in ("paper-manuscript-en-20260910", "paper-supplement-en-20260910"):
    doc = fitz.open(OUT / (stem + ".pdf"))
    canvas = Image.new("RGB", (900, 455 * ((len(doc) + 2) // 3)), "#cccccc")
    draw = ImageDraw.Draw(canvas)
    pages = []
    for index, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(0.48, 0.48), alpha=False)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        x, y = (index % 3) * 300, (index // 3) * 455
        canvas.paste(im, (x, y + 20))
        draw.text((x + 8, y + 3), f"Page {index + 1}", fill="black")
        words = page.get_text("words")
        outside = [w[:4] for w in words if w[0] < 0 or w[1] < 0 or w[2] > page.rect.width + 1 or w[3] > page.rect.height + 1]
        assert not outside, (stem, index, outside)
        text = page.get_text()
        assert "\ufffd" not in text
        pages.append({"page": index + 1, "word_count": len(words), "first_text": text[:100], "out_of_page_words": len(outside)})
    canvas.save(OUT / (stem + "-contact.png"))
    reports.append({"file": stem + ".pdf", "pages": len(doc), "page_checks": pages})
(OUT / "pdf-check.json").write_text(json.dumps(reports, indent=2))
print(json.dumps(reports, indent=2))
