"""Documents (SPEC 5.4): text from .docx, .doc, .rtf, .odt, .pdf, and text files, and the
bridge calls that open them. All documents are synthetic and built at test time."""

import base64
import io
import subprocess
import sys
import zipfile

import pytest

from localdeid.app import documents
from localdeid.app.documents import extract, normalize
from localdeid.app.session import SessionError

from test_app_api import api  # noqa: F401 (fixture)

mac = pytest.mark.skipif(sys.platform != "darwin", reason="macOS textutil and PDFKit")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
MATH = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def docx(body: str, *, extra_ns: str = "", numbering: str = "", styles: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if numbering:
            z.writestr("word/numbering.xml", f'<?xml version="1.0"?><w:numbering {W}>{numbering}</w:numbering>')
        if styles:
            z.writestr("word/styles.xml", f'<?xml version="1.0"?><w:styles {W}>{styles}</w:styles>')
        z.writestr("word/document.xml",
                   f'<?xml version="1.0" encoding="UTF-8"?><w:document {W} {extra_ns}><w:body>{body}'
                   "<w:sectPr/></w:body></w:document>")
    return buf.getvalue()


def p(*runs: str) -> str:
    return "<w:p><w:pPr><w:spacing/></w:pPr>" + "".join(runs) + "</w:p>"


def r(text: str) -> str:
    return f"<w:r><w:rPr><w:b/></w:rPr><w:t xml:space='preserve'>{text}</w:t></w:r>"


def tc(*blocks: str) -> str:
    return "<w:tc><w:tcPr/>" + "".join(blocks) + "</w:tc>"


def tbl(*rows: str) -> str:
    return "<w:tbl><w:tblPr/>" + "".join(rows) + "</w:tbl>"


def tr(*cells: str) -> str:
    return "<w:tr><w:trPr/>" + "".join(cells) + "</w:tr>"


# ------------------------------------------------------------------ .docx


def test_docx_paragraphs_and_table_rows_are_tab_separated():
    body = (p(r("Evaluación de "), r("Mateo Ruiz López"), r(".")) +
            tbl(tr(tc(p(r("Name"))), tc(p(r("DOB"))), tc(p(r("School")))),
                tr(tc(p(r("Sofía Pérez"))), tc(p(r("2/2/2022"))), tc(p(r("Briarwood Elementary"))))) +
            p(r("Mother: Lucía Ruiz. Phone (919) 555-0142.")))
    assert extract(".docx", docx(body)) == ("Evaluación de Mateo Ruiz López.\nName\tDOB\tSchool\n"
                                            "Sofía Pérez\t2/2/2022\tBriarwood Elementary\n"
                                            "Mother: Lucía Ruiz. Phone (919) 555-0142.")


def test_docx_tabs_breaks_and_hyphens_in_paragraphs():
    body = p(r("Name:"), "<w:r><w:tab/></w:r>", r("Mateo"), "<w:r><w:br/></w:r>", r("DOB:"),
             "<w:r><w:tab/></w:r>", r("3/4/2022"), "<w:r><w:t>co</w:t><w:noBreakHyphen/><w:t>op</w:t>"
             "<w:softHyphen/><w:t>erative</w:t></w:r>")
    assert extract(".docx", docx(body)) == "Name:\tMateo\nDOB:\t3/4/2022co-operative"


def test_docx_cell_tabs_and_paragraphs_never_break_the_row():
    body = tbl(tr(tc(p(r("Parents")), p(r("Lucía"), "<w:r><w:tab/></w:r>", r("Ruiz"))), tc(p(r("x")))))
    assert extract(".docx", docx(body)) == "Parents Lucía Ruiz\tx"


def test_docx_skipped_and_merged_columns_keep_values_under_their_headers():
    header = tr(tc(p(r("Name"))), tc(p(r("School"))), tc(p(r("Phone"))))
    skipped = ("<w:tr><w:trPr><w:gridBefore w:val='1'/></w:trPr>" + tc(p(r("Briarwood Elementary"))) +
               tc(p(r("(919) 555-0142"))) + "</w:tr>")
    merged = ("<w:tr>" + "<w:tc><w:tcPr><w:gridSpan w:val='2'/></w:tcPr>" + p(r("Mateo Ruiz")) + "</w:tc>" +
              tc(p(r("(919) 555-0199"))) + "</w:tr>")
    short = "<w:tr><w:trPr><w:gridAfter w:val='2'/></w:trPr>" + tc(p(r("Sofía"))) + "</w:tr>"
    assert extract(".docx", docx(tbl(header, skipped, merged, short))).split("\n") == [
        "Name\tSchool\tPhone", "\tBriarwood Elementary\t(919) 555-0142", "Mateo Ruiz\t\t(919) 555-0199", "Sofía\t\t"]


def test_docx_tracked_changes_moves_and_field_codes():
    body = p(r("Seen at "), "<w:del><w:r><w:delText>Oak Clinic</w:delText></w:r></w:del>",
             "<w:ins><w:r><w:t>Pine Clinic</w:t></w:r></w:ins>", r(" on "),
             "<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:instrText> DATE \\@ M/d/yyyy </w:instrText></w:r>"
             "<w:r><w:fldChar w:fldCharType='separate'/></w:r>", r("3/4/2026"),
             "<w:r><w:fldChar w:fldCharType='end'/></w:r>", r("."))
    body += (p("<w:moveFrom><w:r><w:t>Old place.</w:t></w:r></w:moveFrom>") +
             p("<w:moveTo><w:r><w:t>New place.</w:t></w:r></w:moveTo>"))
    assert extract(".docx", docx(body)) == "Seen at Pine Clinic on 3/4/2026.\n\nNew place."


def test_docx_content_controls_hyperlinks_and_wrapped_rows_and_cells():
    body = ("<w:sdt><w:sdtPr/><w:sdtContent>" + p(r("Child: Mateo")) + "</w:sdtContent></w:sdt>" +
            p("<w:hyperlink><w:r><w:t>Briarwood site</w:t></w:r></w:hyperlink>",
              "<w:smartTag><w:r><w:t> in Cary</w:t></w:r></w:smartTag>") +
            tbl("<w:sdt><w:sdtContent>" + tr(tc(p(r("A"))), "<w:sdt><w:sdtContent>" + tc(p(r("B"))) +
                                              "</w:sdtContent></w:sdt>") + "</w:sdtContent></w:sdt>") +
            "<w:customXml>" + p(r("Custom block")) + "</w:customXml>")
    assert extract(".docx", docx(body)) == "Child: Mateo\nBriarwood site in Cary\nA\tB\nCustom block"


def test_docx_text_box_read_once_from_choice():
    box = "<w:txbxContent>" + p(r("Child: Mateo Ruiz")) + p(r("DOB 3/4/2022")) + "</w:txbxContent>"
    body = p(r("Cover"), "<w:r><mc:AlternateContent><mc:Choice Requires='wps'><w:drawing>" + box +
             "</w:drawing></mc:Choice><mc:Fallback><w:pict>" + box + "</w:pict></mc:Fallback>"
             "</mc:AlternateContent></w:r>")
    assert extract(".docx", docx(body, extra_ns=MC)) == "Cover\nChild: Mateo Ruiz\nDOB 3/4/2022"


def test_docx_alternate_content_is_read_once_even_with_several_choices():
    box = "<w:txbxContent>" + p(r("Score 8/10 trials")) + "</w:txbxContent>"
    body = p("<w:r><mc:AlternateContent><mc:Choice Requires='wps'>" + box + "</mc:Choice>"
             "<mc:Choice Requires='wpg'>" + box + "</mc:Choice><mc:Fallback>" + box + "</mc:Fallback>"
             "</mc:AlternateContent></w:r>")
    assert extract(".docx", docx(body, extra_ns=MC)) == "Score 8/10 trials"
    only_fallback = p("<w:r><mc:AlternateContent><mc:Fallback>" + box + "</mc:Fallback></mc:AlternateContent></w:r>")
    assert extract(".docx", docx(only_fallback, extra_ns=MC)) == "Score 8/10 trials"
    block = "<mc:AlternateContent><mc:Choice>" + p(r("Once")) + "</mc:Choice><mc:Choice>" + p(r("Once")) + \
        "</mc:Choice></mc:AlternateContent>"
    assert extract(".docx", docx(block, extra_ns=MC)) == "Once"


def test_docx_nested_table_stays_in_its_cell():
    inner = tbl(tr(tc(p(r("in1"))), tc(p(r("in2")))))
    body = tbl(tr(tc(p(r("outer"))), tc(inner)), tr(tc(p(r("next"))), tc(p(r("row")))))
    assert extract(".docx", docx(body)) == "outer\tin1 in2\nnext\trow"


@mac
def test_docx_symbols_are_decoded_or_refused():
    def sym(font, char):
        return f"<w:r><w:sym w:font='{font}' w:char='{char}'/></w:r>"
    body = p(r("Score 85 "), sym("Symbol", "F0B1"), r("2; age "), sym("Symbol", "F0B3"), r("3 "),
             sym("Wingdings", "F0FD"), r(" bilingual "), sym("Wingdings", "F0A8"), r(" interpreter "),
             sym("MS Gothic", "2612"))
    assert extract(".docx", docx(body)) == "Score 85 ±2; age ≥3 ☒ bilingual ◻ interpreter ☒"
    for font, char in (("Wingdings", "F04A"), ("Webdings", "F061"), ("Symbol", "zz")):
        with pytest.raises(SessionError, match="doc-symbols"):
            extract(".docx", docx(p(r("x "), sym(font, char))))


def test_docx_embedded_documents_and_objects_are_refused():
    alt = p(r("Evaluation")) + "<w:altChunk r:id='rId9' xmlns:r='urn:r'/>"
    with pytest.raises(SessionError, match="doc-embedded"):
        extract(".docx", docx(alt))
    obj = p(r("Scores:"), "<w:r><w:object><v:shape xmlns:v='urn:v'/></w:object></w:r>")
    with pytest.raises(SessionError, match="doc-embedded"):
        extract(".docx", docx(obj))


def test_docx_plain_equations_are_read_and_structured_ones_refused():
    eq = "<m:oMath><m:r><m:t>85</m:t></m:r><m:r><m:rPr><m:sty m:val='p'/></m:rPr><m:t>±2</m:t></m:r></m:oMath>"
    assert extract(".docx", docx(p(r("Standard score: "), eq), extra_ns=MATH)) == "Standard score: 85±2"
    block = "<m:oMathPara>" + eq + "</m:oMathPara>"
    assert extract(".docx", docx(block, extra_ns=MATH)) == "85±2"
    frac = "<m:oMath><m:f><m:num><m:r><m:t>8</m:t></m:r></m:num><m:den><m:r><m:t>10</m:t></m:r></m:den></m:f></m:oMath>"
    with pytest.raises(SessionError, match="doc-equation"):
        extract(".docx", docx(p(r("Accuracy "), frac), extra_ns=MATH))


def test_docx_text_in_an_unknown_structure_is_refused_not_dropped():
    body = p(r("Evaluation")) + "<w:unknownBlock><w:r><w:t>Score 8/10</w:t></w:r></w:unknownBlock>"
    with pytest.raises(SessionError, match="doc-incomplete"):
        extract(".docx", docx(body))


def lvl(ilvl, fmt, text, start=1, suff=None, font=None):
    rpr = f"<w:rPr><w:rFonts w:ascii='{font}'/></w:rPr>" if font else ""
    sf = f"<w:suff w:val='{suff}'/>" if suff else ""
    return (f"<w:lvl w:ilvl='{ilvl}'><w:start w:val='{start}'/><w:numFmt w:val='{fmt}'/>{sf}"
            f"<w:lvlText w:val='{text}'/>{rpr}</w:lvl>")


def num_p(num_id, ilvl, text, style=None):
    ppr = f"<w:pStyle w:val='{style}'/>" if style else ""
    if num_id is not None:
        ppr += f"<w:numPr><w:ilvl w:val='{ilvl}'/><w:numId w:val='{num_id}'/></w:numPr>"
    return f"<w:p><w:pPr>{ppr}</w:pPr>{r(text)}</w:p>"


NUMBERING = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "decimal", "%1.") + lvl(1, "lowerLetter", "%2)") +
             lvl(2, "lowerRoman", "%1.%2.%3", suff="space") + "</w:abstractNum>"
             "<w:abstractNum w:abstractNumId='1'>" + lvl(0, "bullet", "\uf0b7", font="Symbol") + "</w:abstractNum>"
             "<w:abstractNum w:abstractNumId='2'>" + lvl(0, "upperRoman", "%1.", start=4) + "</w:abstractNum>"
             "<w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>"
             "<w:num w:numId='2'><w:abstractNumId w:val='1'/></w:num>"
             "<w:num w:numId='3'><w:abstractNumId w:val='0'/><w:lvlOverride w:ilvl='0'>"
             "<w:startOverride w:val='1'/></w:lvlOverride></w:num>"
             "<w:num w:numId='4'><w:abstractNumId w:val='2'/></w:num>")


def test_docx_list_numbers_and_bullets_are_written_like_word():
    body = (num_p(1, 0, "Outcome: Mateo will name 20 objects.") + num_p(1, 1, "at home") + num_p(1, 1, "at daycare") +
            num_p(1, 2, "with a model") + num_p(1, 0, "Outcome: two-word phrases.") + num_p(1, 1, "at meals") +
            num_p(2, 0, "Hanen strategies") + num_p(None, 0, "Plain paragraph.") + num_p(0, 0, "numId 0: no number") +
            num_p(3, 0, "Restarted list") + num_p(4, 0, "Starts at four"))
    assert extract(".docx", docx(body, numbering=NUMBERING)).split("\n") == [
        "1.\tOutcome: Mateo will name 20 objects.", "a)\tat home", "b)\tat daycare", "1.b.i with a model",
        "2.\tOutcome: two-word phrases.", "a)\tat meals", "•\tHanen strategies", "Plain paragraph.",
        "numId 0: no number", "1.\tRestarted list", "IV.\tStarts at four"]


def test_docx_numbering_from_a_paragraph_style():
    styles = ("<w:style w:type='paragraph' w:styleId='ListNumber'><w:pPr><w:numPr><w:numId w:val='1'/></w:numPr>"
              "</w:pPr></w:style><w:style w:type='paragraph' w:styleId='Outcome'><w:basedOn w:val='ListNumber'/>"
              "</w:style>")
    body = num_p(None, 0, "First outcome", style="Outcome") + num_p(None, 0, "Second outcome", style="ListNumber")
    assert extract(".docx", docx(body, numbering=NUMBERING, styles=styles)) == "1.\tFirst outcome\n2.\tSecond outcome"


def test_docx_lvl_restart():
    numbering = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "decimal", "%1.") +
                 lvl(1, "lowerLetter", "%2)").replace("</w:lvl>", "<w:lvlRestart w:val='0'/></w:lvl>") +
                 "</w:abstractNum><w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>")
    body = num_p(1, 0, "Goal") + num_p(1, 1, "step") + num_p(1, 0, "Goal") + num_p(1, 1, "step")
    assert extract(".docx", docx(body, numbering=numbering)).split("\n") == ["1.\tGoal", "a)\tstep", "2.\tGoal",
                                                                             "b)\tstep"]


def test_docx_two_lists_on_one_definition_continue_like_word():
    numbering = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "decimal", "%1.") + "</w:abstractNum>"
                 "<w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>"
                 "<w:num w:numId='2'><w:abstractNumId w:val='0'/></w:num>")
    body = num_p(1, 0, "A") + num_p(1, 0, "B") + p(r("Gap")) + num_p(2, 0, "C")
    assert extract(".docx", docx(body, numbering=numbering)).split("\n") == ["1.\tA", "2.\tB", "Gap", "3.\tC"]


def test_docx_legacy_form_fields():
    def box(state):
        inner = f"<w:checked w:val='{state}'/>" if state is not None else "<w:default w:val='1'/>"
        return (f"<w:r><w:fldChar w:fldCharType='begin'><w:ffData><w:checkBox>{inner}</w:checkBox></w:ffData>"
                "</w:fldChar></w:r><w:r><w:instrText> FORMCHECKBOX </w:instrText></w:r>"
                "<w:r><w:fldChar w:fldCharType='end'/></w:r>")
    body = p(box(1), r(" Bilingual  "), box(0), r(" Interpreter  "), box(None), r(" Default on"))
    assert extract(".docx", docx(body)) == "☒ Bilingual  ☐ Interpreter  ☒ Default on"
    dd = ("<w:r><w:fldChar w:fldCharType='begin'><w:ffData><w:ddList><w:result w:val='1'/>"
          "<w:listEntry w:val='Mild'/><w:listEntry w:val='Severe'/></w:ddList></w:ffData></w:fldChar></w:r>")
    with pytest.raises(SessionError, match="doc-form-word"):
        extract(".docx", docx(p(r("Severity: "), dd)))


def test_docx_sub_level_start_override_applies_after_each_restart():
    numbering = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "decimal", "%1.") + lvl(1, "decimal", "%1.%2.") +
                 "</w:abstractNum><w:num w:numId='1'><w:abstractNumId w:val='0'/><w:lvlOverride w:ilvl='1'>"
                 "<w:startOverride w:val='5'/></w:lvlOverride></w:num>")
    body = num_p(1, 0, "Goal") + num_p(1, 1, "step") + num_p(1, 0, "Goal") + num_p(1, 1, "step")
    assert extract(".docx", docx(body, numbering=numbering)).split("\n") == ["1.\tGoal", "1.5.\tstep", "2.\tGoal",
                                                                             "2.5.\tstep"]


def test_docx_numbering_properties_inherit_separately_and_style_linked_levels():
    numbering = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "decimal", "%1.") +
                 lvl(1, "decimal", "%1.%2.").replace("<w:start ", "<w:pStyle w:val='Step'/><w:start ") +
                 "</w:abstractNum><w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>")
    styles = ("<w:style w:type='paragraph' w:styleId='Goal'><w:pPr><w:numPr><w:numId w:val='1'/></w:numPr></w:pPr>"
              "</w:style><w:style w:type='paragraph' w:styleId='SubGoal'><w:basedOn w:val='Goal'/><w:pPr><w:numPr>"
              "<w:ilvl w:val='1'/></w:numPr></w:pPr></w:style><w:style w:type='paragraph' w:styleId='Step'>"
              "<w:basedOn w:val='Goal'/></w:style>")
    body = num_p(None, 0, "Goal", style="Goal") + num_p(None, 0, "Inherited level", style="SubGoal") + \
        num_p(None, 0, "Linked step", style="Step")
    assert extract(".docx", docx(body, numbering=numbering, styles=styles)).split("\n") == [
        "1.\tGoal", "1.1.\tInherited level", "1.2.\tLinked step"]


def test_docx_unknown_numbering_is_refused():
    numbering = ("<w:abstractNum w:abstractNumId='0'>" + lvl(0, "chineseCounting", "%1") + "</w:abstractNum>"
                 "<w:num w:numId='1'><w:abstractNumId w:val='0'/></w:num>")
    with pytest.raises(SessionError, match="doc-numbering"):
        extract(".docx", docx(num_p(1, 0, "x"), numbering=numbering))
    with pytest.raises(SessionError, match="doc-numbering"):  # a level the list does not define
        extract(".docx", docx(num_p(1, 5, "x"), numbering=NUMBERING))


def test_docx_errors():
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".docx", b"not a zip at all")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", "<x/>")  # an .odt renamed: no word/document.xml
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".docx", buf.getvalue())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document")
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".docx", buf.getvalue())
    with pytest.raises(SessionError, match="doc-empty"):
        extract(".docx", docx(p(r("   "))))


def test_docx_zip_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(documents, "MAX_XML", 1000)
    big = docx(p(r("a" * 5000)))
    assert len(big) < 1000  # small on disk, large inside
    with pytest.raises(SessionError, match="doc-too-large"):
        extract(".docx", big)


def test_docx_entities_are_not_expanded():
    xml = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
           '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
           '<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;"><!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">'
           '<!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;"><!ENTITY h "&g;&g;&g;&g;&g;&g;&g;&g;&g;&g;">]>'
           f'<w:document {W}><w:body><w:p><w:r><w:t>&h;</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    with pytest.raises(SessionError, match="doc-unreadable"):  # expat's amplification limit stops it
        extract(".docx", buf.getvalue())


# ------------------------------------------------------------------ .doc, .rtf, .odt (textutil)


@mac
@pytest.mark.parametrize("fmt", ["doc", "rtf", "odt"])
def test_textutil_formats_keep_accents(tmp_path, fmt):
    src = tmp_path / "in.html"
    src.write_text('<html><head><meta charset="utf-8"></head><body><p>Evaluación de Mateo Ruiz López.</p>'
                   "<p>Teléfono (919) 555-0142.</p></body></html>", encoding="utf-8")
    out = tmp_path / f"out.{fmt}"
    subprocess.run(["/usr/bin/textutil", "-convert", fmt, str(src), "-output", str(out)], check=True)
    text = extract(f".{fmt}", out.read_bytes())
    assert "Evaluación de Mateo Ruiz López." in text and "Teléfono (919) 555-0142." in text


@mac
def test_textutil_failure_is_unreadable(monkeypatch):
    monkeypatch.setattr(documents, "TEXTUTIL", "/nonexistent/textutil")
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".rtf", b"{\\rtf1 hi}")


# ------------------------------------------------------------------ .pdf (PDFKit)


def _pdf_page(text: str) -> bytes:
    from AppKit import NSMakeRect, NSTextView

    view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 612, 792))
    view.setString_(text)
    return bytes(view.dataWithPDFInsideRect_(view.bounds()))


def _pdf(pages: list[str], tmp_path=None, **options) -> bytes:
    import Quartz
    from Foundation import NSData

    first = _pdf_page(pages[0])
    doc = Quartz.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(first, len(first)))
    for i, text in enumerate(pages[1:], start=1):
        more = _pdf_page(text)
        other = Quartz.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(more, len(more)))
        doc.insertPage_atIndex_(other.pageAtIndex_(0), i)
    if not options:
        return bytes(doc.dataRepresentation())
    path = tmp_path / "locked.pdf"
    opts = {}
    if "user" in options:
        opts[Quartz.PDFDocumentUserPasswordOption] = options["user"]
    if "owner" in options:
        opts[Quartz.PDFDocumentOwnerPasswordOption] = options["owner"]
    assert doc.writeToFile_withOptions_(str(path), opts)
    return path.read_bytes()


def _image_only_pdf() -> bytes:
    import Quartz
    from Foundation import NSMutableData

    data = NSMutableData.data()
    consumer = Quartz.CGDataConsumerCreateWithCFData(data)
    ctx = Quartz.CGPDFContextCreate(consumer, Quartz.CGRectMake(0, 0, 612, 792), None)
    Quartz.CGPDFContextBeginPage(ctx, None)
    Quartz.CGContextSetRGBFillColor(ctx, 0.2, 0.2, 0.2, 1)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(72, 600, 300, 40))  # a "scanned line"
    Quartz.CGPDFContextEndPage(ctx)
    Quartz.CGPDFContextClose(ctx)
    return bytes(data)


@mac
def test_pdf_pages_in_order_with_accents():
    text = extract(".pdf", _pdf(["Evaluación de Mateo Ruiz López.", "Page two: Lucía Ruiz (919) 555-0142."]))
    assert text.index("Mateo Ruiz López") < text.index("Page two") and "Lucía Ruiz" in text


@mac
def test_pdf_scan_is_refused():
    with pytest.raises(SessionError, match="doc-scanned"):
        extract(".pdf", _image_only_pdf())


def _blank_pdf_page() -> bytes:
    import Quartz
    from Foundation import NSMutableData

    data = NSMutableData.data()
    ctx = Quartz.CGPDFContextCreate(Quartz.CGDataConsumerCreateWithCFData(data), Quartz.CGRectMake(0, 0, 612, 792), None)
    Quartz.CGPDFContextBeginPage(ctx, None)
    Quartz.CGPDFContextEndPage(ctx)
    Quartz.CGPDFContextClose(ctx)
    return bytes(data)


def _join(*pdfs: bytes) -> bytes:
    import Quartz
    from Foundation import NSData

    docs = [Quartz.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(b, len(b))) for b in pdfs]
    for other in docs[1:]:
        docs[0].insertPage_atIndex_(other.pageAtIndex_(0), docs[0].pageCount())
    return bytes(docs[0].dataRepresentation())


@mac
def test_pdf_with_a_scanned_page_among_text_pages_is_refused():
    with pytest.raises(SessionError, match="doc-part-scanned"):
        extract(".pdf", _join(_pdf(["Page one about Mateo."]), _image_only_pdf()))


def _pale_pdf() -> bytes:
    import Quartz
    from Foundation import NSMutableData

    data = NSMutableData.data()
    ctx = Quartz.CGPDFContextCreate(Quartz.CGDataConsumerCreateWithCFData(data), Quartz.CGRectMake(0, 0, 612, 792), None)
    Quartz.CGPDFContextBeginPage(ctx, None)
    Quartz.CGContextSetRGBStrokeColor(ctx, 0.92, 0.92, 0.92, 1)  # pale gray, thin: a faint scan
    Quartz.CGContextSetLineWidth(ctx, 0.5)
    Quartz.CGContextStrokeRect(ctx, Quartz.CGRectMake(100, 300, 200, 20))
    Quartz.CGPDFContextEndPage(ctx)
    Quartz.CGPDFContextClose(ctx)
    return bytes(data)


@mac
def test_pdf_pale_thin_marks_count_as_a_picture():
    with pytest.raises(SessionError, match="doc-part-scanned"):
        extract(".pdf", _join(_pdf(["Page one about Mateo."]), _pale_pdf()))


@mac
def test_pdf_scan_with_a_filled_field_is_still_refused():
    import Quartz
    from Foundation import NSData, NSMakeRect

    scan = _image_only_pdf()
    doc = Quartz.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(scan, len(scan)))
    field = Quartz.PDFAnnotation.alloc().initWithBounds_forType_withProperties_(
        NSMakeRect(72, 100, 200, 20), Quartz.PDFAnnotationSubtypeWidget, None)
    field.setWidgetFieldType_(Quartz.PDFAnnotationWidgetSubtypeText)
    field.setFieldName_("Child Name")
    field.setWidgetStringValue_("Mateo Ruiz")
    doc.pageAtIndex_(0).addAnnotation_(field)
    with pytest.raises(SessionError, match="doc-scanned"):
        extract(".pdf", bytes(doc.dataRepresentation()))


@mac
def test_pdf_blank_page_is_fine():
    text = extract(".pdf", _join(_pdf(["Page one about Mateo."]), _blank_pdf_page(), _pdf(["Page three."])))
    assert "Page one about Mateo." in text and "Page three." in text


@mac
def test_pdf_form_fields_and_text_boxes_are_read():
    import Quartz
    from Foundation import NSData, NSMakeRect

    base = _pdf(["Daily note. Goal data below."])
    doc = Quartz.PDFDocument.alloc().initWithData_(NSData.dataWithBytes_length_(base, len(base)))
    page = doc.pageAtIndex_(0)

    def widget(kind, name, rect):
        a = Quartz.PDFAnnotation.alloc().initWithBounds_forType_withProperties_(
            rect, Quartz.PDFAnnotationSubtypeWidget, None)
        a.setWidgetFieldType_(kind)
        a.setFieldName_(name)
        page.addAnnotation_(a)
        return a

    widget(Quartz.PDFAnnotationWidgetSubtypeText, "Child Name", NSMakeRect(72, 600, 200, 20)) \
        .setWidgetStringValue_("Mateo Ruiz")
    widget(Quartz.PDFAnnotationWidgetSubtypeText, "Score", NSMakeRect(72, 570, 200, 20)) \
        .setWidgetStringValue_("8/10 trials")
    widget(Quartz.PDFAnnotationWidgetSubtypeText, "Empty", NSMakeRect(72, 540, 200, 20))
    box = widget(Quartz.PDFAnnotationWidgetSubtypeButton, "Bilingual", NSMakeRect(72, 510, 12, 12))
    box.setWidgetControlType_(Quartz.kPDFWidgetCheckBoxControl)
    box.setButtonWidgetState_(1)
    menu = widget(Quartz.PDFAnnotationWidgetSubtypeChoice, "Fluency", NSMakeRect(300, 600, 120, 20))
    menu.setChoices_(["Typical", "Mild stutter", "Severe stutter"])
    menu.setValues_(["0", "1", "2"])
    menu.setWidgetStringValue_("2")
    for option, on in (("Mild", 0), ("Severe", 1)):
        radio = widget(Quartz.PDFAnnotationWidgetSubtypeButton, "Severity", NSMakeRect(300, 510, 12, 12))
        radio.setWidgetControlType_(Quartz.kPDFWidgetRadioButtonControl)
        radio.setWidgetOnStateString_(option)
        radio.setButtonWidgetState_(on)
    free = Quartz.PDFAnnotation.alloc().initWithBounds_forType_withProperties_(
        NSMakeRect(72, 450, 300, 40), Quartz.PDFAnnotationSubtypeFreeText, None)
    free.setContents_("Typed note: Lucía called (919) 555-0142.")
    page.addAnnotation_(free)
    text = extract(".pdf", bytes(doc.dataRepresentation()))
    assert "Daily note. Goal data below." in text
    assert "Severity: Mild" not in text and "Severity: checked" not in text
    for line in ("Child Name: Mateo Ruiz", "Score: 8/10 trials", "Bilingual: checked", "Severity: Severe",
                 "Fluency: Severe stutter",
                 "Typed note: Lucía called (919) 555-0142."):
        assert line in text.split("\n"), (line, text)
    assert "Empty:" not in text


def _raw_pdf(widgets: list[str], text: str = "Daily note.") -> bytes:
    """A one-page PDF with Helvetica text and form widgets given as PDF dictionary bodies."""
    n = len(widgets)
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [f"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [{' '.join(f'{5 + i} 0 R' for i in range(n))}] >> >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 {5 + n} 0 R >> >> "
            f"/Annots [{' '.join(f'{5 + i} 0 R' for i in range(n))}] >>",
            None,
            *[f"<< /Type /Annot /Subtype /Widget /P 3 0 R {w} >>" for w in widgets],
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.7\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        if body is None:
            out += f"{i} 0 obj << /Length {len(content)} >> stream\n".encode() + content + b"\nendstream endobj\n"
        else:
            out += f"{i} 0 obj {body} endobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@mac
def test_pdf_choice_fields_give_the_shown_label():
    data = _raw_pdf(["/FT /Ch /Ff 131072 /T (Severity) /Rect [72 500 300 520] /Opt [[(1) (2)] [(2) (Severe)]] /V (2)",
                     "/FT /Ch /T (Setting) /Rect [72 400 300 460] /Opt [(Home) (Daycare)] /V (Daycare)"])
    lines = extract(".pdf", data).split("\n")
    assert "Severity: Severe" in lines and "Setting: Daycare" in lines and "Severity: 2" not in lines


@mac
def test_pdf_multi_select_list_is_refused():
    data = _raw_pdf(["/FT /Ch /Ff 2097152 /T (Services) /Rect [72 600 300 660] "
                     "/Opt [(Speech therapy) (Physical therapy) (OT)] /V [(Speech therapy) (Physical therapy)]"])
    with pytest.raises(SessionError, match="doc-form"):
        extract(".pdf", data)


@mac
def test_pdf_with_open_password_is_refused(tmp_path):
    with pytest.raises(SessionError, match="doc-locked"):
        extract(".pdf", _pdf(["Secret note for Mateo."], tmp_path, user="pw", owner="own"))


@mac
def test_pdf_with_owner_password_only_opens(tmp_path):
    assert "Mateo" in extract(".pdf", _pdf(["Note for Mateo."], tmp_path, owner="own"))


@mac
def test_pdf_garbage_is_unreadable():
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".pdf", b"no pdf header here")
    with pytest.raises(SessionError, match="doc-unreadable"):
        extract(".pdf", b"%PDF-1.7\nbroken")


# ------------------------------------------------------------------ text files and rules


@pytest.mark.parametrize("data", ["Evaluación de Mateo.\r\nLínea 2".encode("utf-8"),
                                  "﻿Evaluación de Mateo.\r\nLínea 2".encode("utf-8"),
                                  "Evaluación de Mateo.\r\nLínea 2".encode("utf-16"),
                                  "Evaluación de Mateo.\r\nLínea 2".encode("cp1252")])
def test_plain_text_encodings(data):
    assert extract(".txt", data) == "Evaluación de Mateo.\nLínea 2"


def test_normalize():
    assert normalize("a\r\nb\rc\fd\x00e\x07\t f  \n\n\n\ng h") == "a\nb\nc\nde\t f\n\ng\nh"


def test_rules_for_every_kind():
    with pytest.raises(SessionError, match="doc-unsupported"):
        extract(".pages", b"x")
    with pytest.raises(SessionError, match="doc-unsupported"):
        extract("", b"x")
    with pytest.raises(SessionError, match="doc-empty"):
        extract(".txt", b"")
    with pytest.raises(SessionError, match="doc-empty"):
        extract(".txt", b" \n\t\n")
    with pytest.raises(SessionError, match="doc-too-large"):
        extract(".txt", b"a" * (documents.MAX_BYTES + 1))
    assert extract(".TXT", b"Mateo") == "Mateo"  # the page lowercases; the app does too


# ------------------------------------------------------------------ bridge


def test_open_document_opens_check_like_a_paste(api):  # noqa: F811
    body = p(r("Name: Quillon Abernathy")) + tbl(tr(tc(p(r("Phone"))), tc(p(r("(919) 555-0142")))))
    out = api.open_document(".docx", base64.b64encode(docx(body)).decode())
    assert out["ok"], out
    hidden = {row["value"] for row in out["view"]["rows"] if row["hidden"]}
    assert {"Quillon", "Abernathy", "(919) 555-0142"} <= hidden
    pasted = api.paste("Name: Quillon Abernathy\nPhone\t(919) 555-0142")
    assert [(r_["value"], r_["hidden"]) for r_ in pasted["view"]["rows"]] == \
        [(r_["value"], r_["hidden"]) for r_ in out["view"]["rows"]]


def test_open_document_errors_are_plain(api):  # noqa: F811
    for args, code in (((".pages", "eA=="), "doc-unsupported"), ((".txt", "not base64!"), "doc-unreadable"),
                       ((".txt", 5), "bad-request"), ((None, "eA=="), "bad-request"),
                       ((".txt", "A" * (documents.MAX_BYTES * 2)), "doc-too-large")):
        out = api.open_document(*args)
        assert not out["ok"] and out["error"] == code and out["message"], (args, out)
    assert api.open_document(".pdf", "")["error"] == "doc-empty"


def test_delete_everything_while_reading_cancels_the_import(api, monkeypatch):  # noqa: F811
    real = documents.extract

    def slow_extract(ext, data):  # Delete everything finishes while the document is read
        assert api.delete_everything("DELETE")["ok"]
        return real(ext, data)
    monkeypatch.setattr(documents, "extract", slow_extract)
    out = api.open_document(".txt", base64.b64encode(b"Name: Quillon Abernathy").decode())
    assert out["error"] == "deleted-meanwhile" and api._m.history() == []
    api._set_chooser(lambda: (".txt", b"Name: Quillon Abernathy"))
    assert api.choose_document()["error"] == "deleted-meanwhile" and api._m.history() == []
    monkeypatch.setattr(documents, "extract", real)
    assert api.open_document(".txt", base64.b64encode(b"Name: Quillon Abernathy").decode())["ok"]
    # the page read the count before reading the file; a Delete everything since then wins
    seen = api.state()["wipes"]
    assert api.delete_everything("DELETE")["ok"]
    out = api.open_document(".txt", base64.b64encode(b"Name: Quillon Abernathy").decode(), seen)
    assert out["error"] == "deleted-meanwhile" and api._m.history() == []
    assert api.open_document(".txt", base64.b64encode(b"Name: Quillon Abernathy").decode(),
                             api.state()["wipes"])["ok"]


def test_choose_document(api):  # noqa: F811
    assert api.choose_document()["error"] == "bad-request"  # no window: no dialog
    api._set_chooser(lambda: None)
    assert api.choose_document() == {"ok": True, "cancelled": True}
    api._set_chooser(lambda: (".txt", "Name: Quillon Abernathy\nCame today.".encode()))
    out = api.choose_document()
    assert out["ok"] and {"Quillon", "Abernathy"} <= {r_["value"] for r_ in out["view"]["rows"] if r_["hidden"]}

    def refuse():
        raise SessionError("doc-too-large")
    api._set_chooser(refuse)
    assert api.choose_document()["error"] == "doc-too-large"
