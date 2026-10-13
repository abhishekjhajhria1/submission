"""Write the farmer's scanned receipts into workspace/receipts as real PDF files.

A tiny dependency-free PDF writer: one page, Courier text lines. Run directly or
let the farm app call ensure_files() at startup.
"""
from pathlib import Path

from .seed import RECEIPTS

WORKSPACE = Path(__file__).resolve().parent.parent / "workspace"


def _pdf(lines: list[tuple[str, int, str]]) -> bytes:
    ops, y = [], 800
    for style, size, text in lines:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        font = "F2" if style == "B" else "F1"
        ops.append(f"BT /{font} {size} Tf 50 {y} Td ({safe}) Tj ET")
        y -= size + 8
    stream = "\n".join(ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def ensure_files() -> None:
    folder = WORKSPACE / "receipts"
    folder.mkdir(parents=True, exist_ok=True)
    for name, lines in RECEIPTS.items():
        path = folder / name
        if not path.exists():
            path.write_bytes(_pdf(lines))


if __name__ == "__main__":
    ensure_files()
    print("receipts written to", WORKSPACE / "receipts")
