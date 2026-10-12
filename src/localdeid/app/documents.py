"""Text from a dropped or opened document (SPEC 5.4, DECISIONS 27).

The page sends the file's extension and bytes, never its name (a filename can hold a
child's name). Nothing here writes to disk. The result is plain text that goes
through the same checks as a paste.

- .docx: our own reader. Body text in document order; each table row is one line of
  tab-separated cells, the same shape as a table pasted from Word, so the fields
  detector reads it the same way. Content controls, insertions, hyperlinks, and text
  boxes are read (one representation of each), and symbol characters (Symbol font,
  Wingdings check boxes) are decoded; tracked deletions, the old place of moved text, and
  field codes are not. Pictures are not read. Automatic list numbers and bullets are
  written as Word shows them ("1.<TAB>", "a)<TAB>", "•<TAB>"). Equations made of plain
  runs are read as their text. A document with an embedded document or object, a structured equation, a
  symbol that cannot be named, or any visible text this reader did not take is refused
  rather than read in part. Headers, footers, footnotes, and comments are
  not read (as when copying the body).
- .doc, .rtf, .odt: macOS textutil, through stdin and stdout.
- .pdf: PDFKit, plus filled form fields and typed text boxes. A password-protected PDF,
  and a PDF with any scanned or picture-only page (words that cannot be checked), are
  refused.
- .txt, .md: UTF-8 (with or without a BOM), UTF-16 with a BOM, else Windows-1252.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from contextvars import ContextVar
import subprocess
import zipfile
from xml.etree import ElementTree

from .session import SessionError

MAX_BYTES = 25 * 1024 * 1024  # a long evaluation as .docx or .pdf is well under 5 MB
MAX_XML = 50 * 1024 * 1024  # uncompressed word/document.xml (zip bomb guard)
TEXTUTIL = "/usr/bin/textutil"
TEXTUTIL_FORMATS = {".doc": "doc", ".rtf": "rtf", ".odt": "odt"}
PLAIN = (".txt", ".md")
EXTENSIONS = (".docx", ".pdf", *TEXTUTIL_FORMATS, *PLAIN)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
# An equation made only of runs ("85 ± 2") is read as its text; any structure (fraction,
# root, script, matrix...) is refused: its text alone would change its meaning.
_PLAIN_MATH = {f"{M}{n}" for n in ("oMathPara", "oMathParaPr", "oMath", "r", "t", "rPr", "ctrlPr", "sty", "scr",
                                    "nor", "lit", "brk", "aln", "jc")}
_READ: ContextVar[list[str]] = ContextVar("_READ")  # every w:t and m:t text the reader took
_NUM: ContextVar["_Numbering"] = ContextVar("_NUM")  # the document's list numbering
_SKIP = {f"{W}del", f"{W}moveFrom", f"{W}delText", f"{W}instrText",
         f"{W}pPr", f"{W}rPr", f"{W}tblPr", f"{W}trPr", f"{W}tcPr", f"{W}sectPr"}
# Word checkbox and check-mark symbols in Wingdings (the ones Word's Insert Symbol uses for
# forms); any other Wingdings or private-font symbol makes the document unreadable here.
_WINGDINGS = {0xA8: "\u25fb", 0xFB: "\u2718", 0xFC: "\u2714", 0xFD: "\u2612", 0xFE: "\u2611"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def extract(ext: str, data: bytes) -> str:
    """The document's text, or SessionError with a doc-* code."""
    ext = str(ext or "").lower()
    if ext not in EXTENSIONS:
        raise SessionError("doc-unsupported")
    if len(data) > MAX_BYTES:
        raise SessionError("doc-too-large")
    if not data:
        raise SessionError("doc-empty")
    if ext == ".docx":
        text = _docx(data)
    elif ext == ".pdf":
        text = _pdf(data)
    elif ext in TEXTUTIL_FORMATS:
        text = _textutil(data, TEXTUTIL_FORMATS[ext])
    else:
        text = _plain(data)
    text = normalize(text)
    if not text.strip():
        raise SessionError("doc-empty")
    return text


def normalize(text: str) -> str:
    """LF line ends, no control characters except tab and newline, no trailing spaces,
    at most one blank line in a row."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = text.replace(" ", "\n").replace(" ", "\n").replace("﻿", "")
    text = _CONTROL.sub("", text)
    text = "\n".join(line.rstrip(" ") for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


# ------------------------------------------------------------------ .docx


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root = _part(z, "word/document.xml")
            numbering = _part(z, "word/numbering.xml", optional=True)
            styles = _part(z, "word/styles.xml", optional=True)
    except SessionError:
        raise
    except Exception:  # noqa: BLE001 (not a zip, no document.xml, bad XML, bad compression)
        raise SessionError("doc-unreadable") from None
    body = root.find(f"{W}body")
    if body is None:
        raise SessionError("doc-unreadable")
    read: list[str] = []
    token, num_token = _READ.set(read), _NUM.set(_Numbering(numbering, styles))
    try:
        text = "\n".join(_blocks(body))
    finally:
        _READ.reset(token)
        _NUM.reset(num_token)
    # Every visible character must have been read: a structure this reader does not
    # know makes the document unreadable here, never silently shorter.
    if _letters(_visible(body)) != _letters(read):
        raise SessionError("doc-incomplete")
    return text


def _part(z: zipfile.ZipFile, name: str, optional: bool = False):
    """One XML part of the package, parsed, with the size cap."""
    try:
        info = z.getinfo(name)
    except KeyError:
        if optional:
            return None
        raise
    if info.file_size > MAX_XML:
        raise SessionError("doc-too-large")
    with z.open(info) as f:
        xml = f.read(MAX_XML + 1)
    if len(xml) > MAX_XML:
        raise SessionError("doc-too-large")
    return ElementTree.fromstring(xml)


class _Numbering:
    """Word's automatic list labels ("1.", "a)", "ii.", "•"), counted in document order
    the way Word shows them, so a numbered outcome list keeps its numbers. A format we
    cannot write is refused, never dropped."""

    def __init__(self, numbering, styles):
        self.levels: dict[str, dict[int, object]] = {}  # abstractNumId -> ilvl -> w:lvl
        self.nums: dict[str, tuple[str, dict[int, int], dict[int, object]]] = {}
        self.counts: dict[str, dict[int, int]] = {}  # abstractNumId -> ilvl -> last number
        self.started: set[str] = set()
        self.style_num: dict[str, tuple[str | None, int | None]] = {}
        self.based_on: dict[str, str] = {}
        if numbering is not None:
            for an in numbering.findall(f"{W}abstractNum"):
                self.levels[_val(an, "abstractNumId")] = {int(_val(lvl, "ilvl") or 0): lvl
                                                          for lvl in an.findall(f"{W}lvl")}
            for num in numbering.findall(f"{W}num"):
                abstract = num.find(f"{W}abstractNumId")
                starts, lvls = {}, {}
                for over in num.findall(f"{W}lvlOverride"):
                    ilvl = int(_val(over, "ilvl") or 0)
                    so = over.find(f"{W}startOverride")
                    if so is not None:
                        starts[ilvl] = int(_val(so, "val") or 1)
                    if over.find(f"{W}lvl") is not None:
                        lvls[ilvl] = over.find(f"{W}lvl")
                self.nums[_val(num, "numId")] = (_val(abstract, "val") if abstract is not None else "", starts, lvls)
        if styles is not None:
            for st in styles.findall(f"{W}style"):
                sid = _val(st, "styleId")
                based = st.find(f"{W}basedOn")
                if based is not None:
                    self.based_on[sid] = _val(based, "val")
                num_pr = st.find(f"{W}pPr/{W}numPr")
                if num_pr is not None:
                    self.style_num[sid] = _num_pr(num_pr)

    def label(self, p) -> str:
        num_id, ilvl, chain = self._num_for(p)
        if not num_id or num_id == "0" or num_id not in self.nums:
            return ""  # Word shows no number for these
        abstract, starts, over = self.nums[num_id]
        levels = {**self.levels.get(abstract, {}), **over}
        if ilvl is None:  # a level linked to the paragraph's style (w:lvl/w:pStyle), else 0
            ilvl = next((k for k, v in sorted(levels.items())
                         if _val(v.find(f"{W}pStyle"), "val") in chain and chain), 0)

        def start_for(level: int) -> int:  # this list's override, else the level's start
            return starts[level] if level in starts else (_start(levels[level]) if level in levels else 1)
        lvl = levels.get(ilvl)
        if lvl is None:
            raise SessionError("doc-numbering")
        counts = self.counts.setdefault(abstract, {})
        if num_id not in self.started:
            self.started.add(num_id)
            for level, start in starts.items():  # a restarted list ("start at 1 again")
                counts[level] = start - 1
                for deeper in [k for k in counts if k > level]:
                    del counts[deeper]
        counts[ilvl] = counts.get(ilvl, start_for(ilvl) - 1) + 1
        for deeper in [k for k in counts if k > ilvl]:
            # a deeper level restarts unless its w:lvlRestart says otherwise (0: never;
            # n: only when level n or a higher one is used)
            restart_el = levels[deeper].find(f"{W}lvlRestart") if deeper in levels else None
            restart = int(_val(restart_el, "val") or 0) if restart_el is not None else None
            if restart is None or (restart != 0 and ilvl < restart):
                del counts[deeper]
        fmt = _val(lvl.find(f"{W}numFmt"), "val") or "decimal"
        text = _val(lvl.find(f"{W}lvlText"), "val") or ""
        if fmt == "bullet":
            label = "•" if text else ""
        else:
            legal = lvl.find(f"{W}isLgl") is not None

            def number(m):
                k = int(m.group(1)) - 1
                k_lvl = levels.get(k)
                n = counts.get(k, start_for(k))
                k_fmt = "decimal" if legal or k_lvl is None else (_val(k_lvl.find(f"{W}numFmt"), "val") or "decimal")
                return _format_number(n, k_fmt)
            label = re.sub(r"%([1-9])", number, text)
        if not label:
            return ""
        suffix = _val(lvl.find(f"{W}suff"), "val") or "tab"
        return label + {"tab": "\t", "space": " "}.get(suffix, "")

    def _num_for(self, p) -> tuple[str | None, int | None, list[str]]:
        """(numId, level or None, the paragraph's style and the styles it is based on).
        numId and level are each taken from the paragraph, else the nearest style that
        sets it."""
        chain: list[str] = []
        style = p.find(f"{W}pPr/{W}pStyle")
        sid = _val(style, "val") if style is not None else None
        while sid and sid not in chain:
            chain.append(sid)
            sid = self.based_on.get(sid)
        num_id = ilvl = None
        own = p.find(f"{W}pPr/{W}numPr")
        if own is not None:
            num_id, ilvl = _num_pr(own)
        for sid in chain:
            style_id, style_lvl = self.style_num.get(sid, (None, None))
            num_id = num_id if num_id is not None else style_id
            ilvl = ilvl if ilvl is not None else style_lvl
        return num_id, ilvl, chain


def _val(el, name: str) -> str:
    return "" if el is None else (el.get(f"{W}{name}") or "")


def _num_pr(num_pr) -> tuple[str | None, int | None]:
    nid, ilvl = num_pr.find(f"{W}numId"), num_pr.find(f"{W}ilvl")
    return (_val(nid, "val") if nid is not None else None,
            int(_val(ilvl, "val") or 0) if ilvl is not None else None)


def _start(lvl) -> int:
    try:
        return int(_val(lvl.find(f"{W}start"), "val") or 1)
    except ValueError:
        return 1


def _format_number(n: int, fmt: str) -> str:
    if fmt == "decimal":
        return str(n)
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt in ("lowerLetter", "upperLetter") and n >= 1:
        letter = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)  # a..z, aa, bb, ... (Word)
        return letter if fmt == "lowerLetter" else letter.upper()
    if fmt in ("lowerRoman", "upperRoman") and 1 <= n < 4000:
        roman = ""
        for value, digits in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                              (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
            while n >= value:
                roman, n = roman + digits, n - value
        return roman if fmt == "lowerRoman" else roman.upper()
    if fmt == "ordinal":
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"
    if fmt == "none":
        return ""
    raise SessionError("doc-numbering")


def _letters(texts) -> Counter:
    return Counter(c for t in texts for c in t if not c.isspace())


def _visible(el, out=None) -> list[str]:
    """All w:t and m:t texts under `el` that Word shows (the same skips and choices)."""
    out = [] if out is None else out
    for child in _children(el):
        if child.tag in _SKIP:
            continue
        if child.tag in (f"{W}t", f"{M}t"):
            out.append(child.text or "")
        else:
            _visible(child, out)
    return out


def _equation(el) -> str:
    for node in el.iter():
        if node.tag.startswith(M) and node.tag not in _PLAIN_MATH:
            raise SessionError("doc-equation")
    texts = [t.text or "" for t in el.iter(f"{M}t")]
    _READ.get().extend(texts)
    return "".join(texts)


def _children(el):
    """`el`'s children, with each mc:AlternateContent replaced by the one representation
    it offers first (its first mc:Choice, else mc:Fallback): text boxes appear once."""
    for child in el:
        if child.tag == f"{MC}AlternateContent":
            chosen = next((c for c in child if c.tag in (f"{MC}Choice", f"{MC}Fallback")), None)
            if chosen is not None:
                yield from _children(chosen)
        else:
            yield child


def _blocks(parent) -> list[str]:
    """Lines for the paragraphs and tables inside `parent`, in order. Any other element
    (content control, custom XML, insertion) is transparent."""
    lines: list[str] = []
    for child in _children(parent):
        if child.tag in _SKIP:
            continue
        if child.tag == f"{W}p":
            lines.append(_paragraph(child))
        elif child.tag == f"{W}altChunk":  # another document inside this one: not read here
            raise SessionError("doc-embedded")
        elif child.tag in (f"{M}oMathPara", f"{M}oMath"):
            lines.append(_equation(child))
        elif child.tag == f"{W}tbl":
            lines.extend(_table(child))
        else:
            lines.extend(_blocks(child))
    return lines


def _paragraph(p) -> str:
    parts: list[str] = [_NUM.get().label(p)]
    _runs(p, parts)
    return "".join(parts)


def _runs(el, parts: list[str]) -> None:
    for child in _children(el):
        tag = child.tag
        if tag in _SKIP:
            continue
        if tag == f"{W}t":
            parts.append(child.text or "")
            _READ.get().append(child.text or "")
        elif tag in (f"{M}oMathPara", f"{M}oMath"):
            parts.append(_equation(child))
        elif tag == f"{W}tab":
            parts.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
        elif tag == f"{W}noBreakHyphen":
            parts.append("-")
        elif tag == f"{W}fldChar" and child.find(f"{W}ffData") is not None:
            parts.append(_legacy_field(child.find(f"{W}ffData")))
        elif tag == f"{W}sym":
            parts.append(_symbol(child.get(f"{W}font") or "", child.get(f"{W}char") or ""))
        elif tag in (f"{W}object", f"{W}altChunk"):  # an embedded object (an Excel table): not read here
            raise SessionError("doc-embedded")
        elif tag == f"{W}txbxContent":  # a text box: its own lines, after the anchor's text
            lines = [line for line in _blocks(child) if line.strip()]
            if lines:
                parts.append("\n" + "\n".join(lines) + "\n")
        else:
            _runs(child, parts)


def _legacy_field(ff) -> str:
    """A legacy Word form field (Word 97-2003 forms). A check box shows ☒ or ☐; its state
    is not text. A text field's value is ordinary text after the field code (read as
    usual). A drop-down's shown entry is not stored as text: refused."""
    box = ff.find(f"{W}checkBox")
    if box is not None:
        state = box.find(f"{W}checked")
        if state is None:
            state = box.find(f"{W}default")
        on = state is not None and _val(state, "val") not in ("0", "false", "off")
        return "☒" if on else "☐"
    if ff.find(f"{W}ddList") is not None:
        raise SessionError("doc-form-word")
    return ""


def _symbol(font: str, char: str) -> str:
    """The character a w:sym run shows. Word stores Symbol and Wingdings characters at
    U+F000 + the font's byte. A symbol we cannot name is refused, never dropped."""
    try:
        code = int(char, 16)
    except ValueError:
        raise SessionError("doc-symbols") from None
    if not 0xF000 <= code <= 0xF0FF:
        return chr(code)  # already a real character
    byte = code - 0xF000
    name = font.strip().lower()
    if name == "symbol":
        import CoreFoundation
        from Foundation import NSData, NSString

        enc = CoreFoundation.CFStringConvertEncodingToNSStringEncoding(CoreFoundation.kCFStringEncodingMacSymbol)
        text = NSString.alloc().initWithData_encoding_(NSData.dataWithBytes_length_(bytes([byte]), 1), enc)
        if text:
            return str(text)
    elif name == "wingdings" and byte in _WINGDINGS:
        return _WINGDINGS[byte]
    raise SessionError("doc-symbols")


def _table(tbl) -> list[str]:
    """One line per row, one tab-separated cell per grid column: columns a row skips
    (gridBefore, gridAfter) and the extra columns of a merged cell (gridSpan) are empty
    cells, so every value stays under its header for the fields detector."""
    rows: list[str] = []
    for tr in _within(tbl, f"{W}tr"):
        cells = [""] * _grid(tr.find(f"{W}trPr/{W}gridBefore"))
        for tc in _within(tr, f"{W}tc"):
            text = " ".join(line for line in _blocks(tc) if line.strip())
            cells.append(re.sub(r"\s*[\t\n]+\s*", " ", text).strip())
            cells.extend([""] * (_grid(tc.find(f"{W}tcPr/{W}gridSpan")) - 1))
        cells.extend([""] * _grid(tr.find(f"{W}trPr/{W}gridAfter")))
        rows.append("\t".join(cells))
    return rows


def _grid(el) -> int:
    try:
        return max(0, min(64, int(_val(el, "val") or 0))) if el is not None else 0
    except ValueError:
        return 0


def _within(el, tag: str):
    """Elements named `tag` inside `el`, not looking into nested tables."""
    for child in _children(el):
        if child.tag == tag:
            yield child
        elif child.tag != f"{W}tbl" and child.tag not in _SKIP:
            yield from _within(child, tag)


# ------------------------------------------------------------------ .pdf


def _pdf(data: bytes) -> str:
    if b"%PDF" not in data[:1024]:
        raise SessionError("doc-unreadable")
    import objc
    from Foundation import NSData
    from Quartz import PDFDocument

    with objc.autorelease_pool():
        doc = PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
        if doc is None or doc.pageCount() == 0:
            raise SessionError("doc-unreadable")
        if doc.isLocked() and not doc.unlockWithPassword_(""):
            raise SessionError("doc-locked")
        pages, pictures = [], 0
        for i in range(doc.pageCount()):
            page = doc.pageAtIndex_(i)
            if page is None:
                raise SessionError("doc-unreadable")
            text = str(page.string() or "")
            extra = _annotation_lines(page)
            if not text.strip() and _has_ink(page):
                pictures += 1  # a scanned page or a picture (form fields or not): its words cannot be checked
            pages.append("\n".join([text, *extra]) if extra else text)
    if pictures == len(pages):
        raise SessionError("doc-scanned")
    if pictures:
        raise SessionError("doc-part-scanned")
    return "\n\n".join(pages)


def _annotation_lines(page) -> list[str]:
    """Filled form fields and typed text boxes, which are not part of the page text:
    "Field name: value" lines, then the text boxes. Notes and comments are not read."""
    import Quartz

    lines: list[str] = []
    for a in page.annotations() or []:
        kind = str(a.type() or "")
        if kind == "Widget":
            field = str(a.widgetFieldType() or "").lstrip("/")
            name = re.sub(r"\s+", " ", str(a.fieldName() or "")).strip()
            if field == "Tx":
                value = str(a.widgetStringValue() or "").strip()
            elif field == "Ch":
                value = _choice_label(a)
            elif field == "Btn" and a.buttonWidgetState() == 1 and not a.isSignatureWidget():
                # a radio group names the chosen option; a checkbox is just checked
                radio = a.widgetControlType() == Quartz.kPDFWidgetRadioButtonControl
                value = str(a.widgetOnStateString() or "").strip() if radio else "checked"
                value = value or "selected"
            else:
                value = ""
            line = (f"{name}: {value}" if name else value) if value else ""
            if line and line not in lines:  # a field shown in two places is read once
                lines.append(line)
        elif kind == "FreeText":
            value = str(a.contents() or "").strip()
            if value:
                lines.append(value)
    return lines


def _choice_label(a) -> str:
    """The label a dropdown or list shows. A field may store an export value ("2" for
    "Severe"): /V is mapped through the options by position. A multi-select list is
    refused: PDFKit does not give its selections."""
    keys = a.annotationKeyValues() or {}
    try:
        flags = int(str(keys.get("/Ff") or 0))
    except ValueError:
        flags = 0
    if flags & (1 << 21):  # MultiSelect
        raise SessionError("doc-form")
    stored = keys.get("/V")
    value = (str(stored) if isinstance(stored, str) else str(a.widgetStringValue() or "")).strip()
    labels = [str(c) for c in (a.choices() or [])]
    exports = [str(v) for v in (a.values() or [])]
    if value and exports and len(exports) == len(labels) and value in exports:
        return labels[exports.index(value)].strip()
    return value


def _has_ink(page) -> bool:
    """True when the page shows anything at all when drawn at full size: any pixel that
    is not pure white (pale or thin marks too). Only a truly blank page is False."""
    import Quartz

    box = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
    if box.size.width <= 0 or box.size.height <= 0:
        return False
    scale = min(1.0, 2000.0 / max(box.size.width, box.size.height))  # 1 pixel per point, capped
    w, h = max(1, int(box.size.width * scale)), max(1, int(box.size.height * scale))
    buf = bytearray(b"\xff" * (w * h))
    ctx = Quartz.CGBitmapContextCreate(buf, w, h, 8, w, Quartz.CGColorSpaceCreateDeviceGray(),
                                       Quartz.kCGImageAlphaNone)
    if ctx is None:
        return True  # cannot tell: treat it as a picture (refuse rather than drop words)
    Quartz.CGContextScaleCTM(ctx, scale, scale)
    Quartz.CGContextTranslateCTM(ctx, -box.origin.x, -box.origin.y)
    Quartz.CGContextDrawPDFPage(ctx, page.pageRef())
    Quartz.CGContextFlush(ctx)
    return min(buf) < 255


# ------------------------------------------------------------------ .doc, .rtf, .odt, text


def _textutil(data: bytes, fmt: str) -> str:
    try:
        out = subprocess.run(
            [TEXTUTIL, "-stdin", "-format", fmt, "-convert", "txt", "-stdout", "-encoding", "UTF-8"],
            input=data, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SessionError("doc-unreadable") from None
    if out.returncode != 0:
        raise SessionError("doc-unreadable")
    return out.stdout.decode("utf-8", errors="replace")


def _plain(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
