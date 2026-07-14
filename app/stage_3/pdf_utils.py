"""
pdf_utils.py — финализация PDF: PDF/A метаданные (включая автора и год
из metadata.py), чётность страниц, двустраничный режим, линеаризация.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

RESOURCES = Path(__file__).parent.parent / "resources"
ICC_PATH = RESOURCES / "sRGB.icc"

_XMP_TEMPLATE = """<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
      <pdfaid:part>2</pdfaid:part>
      <pdfaid:conformance>B</pdfaid:conformance>
    </rdf:Description>
    <rdf:Description rdf:about=""
        xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>
        <rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt>
      </dc:title>{creator}{date}
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

_CREATOR_TPL = """
      <dc:creator>
        <rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq>
      </dc:creator>"""

_DATE_TPL = """
      <dc:date>
        <rdf:Seq><rdf:li>{year}</rdf:li></rdf:Seq>
      </dc:date>"""


def make_pdfa(doc, meta: dict) -> None:
    """
    Добавляет XMP (title/creator/date) и OutputIntent sRGB — компоненты
    для соответствия PDF/A-2b.

    Args:
        doc:  открытый документ PyMuPDF
        meta: {"title": ..., "author": ..., "year": ...}
    """
    title = escape(meta.get("title") or "")
    author = meta.get("author") or ""
    year = meta.get("year") or ""

    xmp = _XMP_TEMPLATE.format(
        title=title,
        creator=_CREATOR_TPL.format(author=escape(author)) if author else "",
        date=_DATE_TPL.format(year=escape(year)) if year else "",
    )
    doc.set_xml_metadata(xmp)

    # дублируем в классический docinfo — его показывают все читалки
    doc.set_metadata({
        "title": meta.get("title") or "",
        "author": author,
        "creationDate": f"D:{year}0101000000" if year.isdigit() else "",
    })

    with open(ICC_PATH, "rb") as f:
        icc_data = f.read()

    icc_xref = doc.get_new_xref()
    doc.update_object(icc_xref, "<</N 3>>")
    doc.update_stream(icc_xref, icc_data)

    intent_xref = doc.get_new_xref()
    # ВАЖНО: объект-словарь обязан начинаться с «<<», иначе он битый и
    # PDF/A-читалки не видят OutputIntent (а с ним — цветовой профиль)
    doc.update_object(intent_xref, f"""<<
/Type /OutputIntent
/S /GTS_PDFA1
/OutputConditionIdentifier (sRGB IEC61966-2.1)
/Info (sRGB IEC61966-2.1)
/DestOutputProfile {icc_xref} 0 R
>>""")

    # ключ каталога ставим штатным API: строковый replace(">>", …) заменял
    # ВСЕ «>>» и по ошибке дублировал OutputIntents внутрь /Info
    catalog_xref = doc.pdf_catalog()
    doc.xref_set_key(catalog_xref, "OutputIntents", f"[{intent_xref} 0 R]")


def add_blank_page(doc, position: str = "start") -> None:
    w, h = doc[0].rect.width, doc[0].rect.height
    doc.insert_page(0 if position == "start" else len(doc), width=w, height=h)


def finalize_pdf(output_path: Path, two_page_view: bool = True,
                 linearize: bool = True) -> None:
    """
    Двустраничный режим + линеаризация одним проходом pikepdf.

    Линеаризация выполняется встроенным в pikepdf libqpdf, поэтому
    внешняя утилита qpdf не требуется ни на Windows, ни на Linux.
    """
    import pikepdf
    try:
        with pikepdf.open(str(output_path), allow_overwriting_input=True) as pdf:
            if two_page_view:
                vp = pikepdf.Dictionary(
                    Type=pikepdf.Name("/ViewerPreferences"),
                    PageLayout=pikepdf.Name("/TwoPageRight"),
                    PageMode=pikepdf.Name("/UseNone"),
                )
                pdf.Root["/ViewerPreferences"] = pdf.make_indirect(vp)
                pdf.Root["/PageLayout"] = pikepdf.Name("/TwoPageRight")
            pdf.save(str(output_path), linearize=linearize)
    except Exception as e:
        print(f"  Финализация (two-page/linearize) не выполнена: {e}")


def sanitize_toc(toc: list[list]) -> list[list]:
    """
    fitz.set_toc требует корректной вложенности: уровень может расти
    максимум на 1 за шаг и первый элемент должен быть уровня 1.
    Чинит "прыжки" уровней, не меняя порядок закладок.
    """
    fixed, prev = [], 0
    for level, title, page in toc:
        level = max(1, min(level, prev + 1)) if prev else 1
        fixed.append([level, title, page])
        prev = level
    return fixed
