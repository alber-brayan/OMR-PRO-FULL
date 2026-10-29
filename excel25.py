"""Exportación de resultados OMR sobre la plantilla original de la editorial.

Se modifica el paquete OOXML directamente para conservar la imagen de cabecera,
los cuadros de texto de DrawingML, los formatos y los ajustes de impresión. No
requiere Excel, Node ni una biblioteca de autoría de hojas en el equipo final.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import io
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
from xml.dom import minidom
import zipfile

ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = ROOT / "assets" / "report_template.xlsx"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
HEADERS = ("PUESTO", "APELLIDOS Y NOMBRE", "COLEGIO", "Nº ORDEN", "BUENAS", "MALAS", "BLANCO", "PUNTAJE", "HORA")


def _parse(data: bytes):
    return minidom.parseString(data)


def _xml(doc) -> bytes:
    return doc.toxml(encoding="UTF-8")


def _nodes(node, name: str):
    return list(node.getElementsByTagNameNS(NS, name))


def _element(doc, name: str, attrs=None, value=None, namespace=NS):
    item = doc.createElementNS(namespace, name)
    for key, content in (attrs or {}).items():
        item.setAttribute(key, str(content))
    if value is not None:
        item.appendChild(doc.createTextNode(str(value)))
    return item


def _clear(cell):
    for child in list(cell.childNodes):
        cell.removeChild(child)
    if cell.hasAttribute("t"):
        cell.removeAttribute("t")


def _text(cell, value):
    """Inline-string cells preserve =,+,-,@ prefixes as literal Excel text."""
    _clear(cell)
    if value is None or value == "":
        return
    value = str(value)
    # OOXML/XML 1.0 cannot represent these control characters.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    if len(value) > 32767:
        raise ValueError("Un texto supera el límite de 32767 caracteres de Excel")
    cell.setAttribute("t", "inlineStr")
    inline = _element(cell.ownerDocument, "is")
    node = _element(cell.ownerDocument, "t", value=value)
    node.setAttribute("xml:space", "preserve")
    inline.appendChild(node)
    cell.appendChild(inline)


def _number(cell, value, formula=None):
    _clear(cell)
    if value is None:
        return
    if formula:
        cell.appendChild(_element(cell.ownerDocument, "f", value=formula))
    cell.appendChild(_element(cell.ownerDocument, "v", value=_decimal(value)))


def _decimal(value) -> str:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("El puntaje y los coeficientes deben ser números finitos")
    return format(result.normalize(), "f")


def _coefficients(scoring=None):
    scoring = scoring or {}
    pairs = (("buenas", "correct", 10), ("malas", "incorrect", -1), ("blanco", "blank", 0))
    return tuple(Decimal(_decimal(scoring.get(es, scoring.get(en, default)))) for es, en, default in pairs)


def _time(value):
    """Return (Excel day fraction, seconds); None stays genuinely blank."""
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        seconds = value.hour * 3600 + value.minute * 60 + value.second
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", value)
        if not match:
            raise ValueError("La hora debe tener formato HH:MM")
        hh, minute, sec = (int(match[1]), int(match[2]), int(match[3] or 0))
        if hh > 23 or minute > 59 or sec > 59:
            raise ValueError("La hora de entrega no es válida")
        seconds = hh * 3600 + minute * 60 + sec
    else:
        raise ValueError("La hora debe ser texto HH:MM o un objeto de hora")
    return seconds / 86400, seconds


def _validated(records: Iterable[dict], scoring=None):
    coeff = _coefficients(scoring)
    grouped = defaultdict(list)
    for index, source in enumerate(records):
        if source.get("confirmed") is not True:
            continue
        record = dict(source)
        grade = record.get("grade")
        if isinstance(grade, bool) or str(grade) not in {str(n) for n in range(1, 7)}:
            raise ValueError("Cada registro confirmado necesita un grado de 1 a 6")
        record["grade"] = int(grade)
        for field, label in (("name", "apellidos y nombres"), ("school", "colegio")):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ValueError(f"Falta {label} en un registro confirmado")
        counts = []
        for field in ("correct", "incorrect", "blank"):
            value = record.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 15:
                raise ValueError("Un registro confirmado necesita buenas, malas y blancos enteros")
            counts.append(value)
        if sum(counts) != 15:
            raise ValueError("Buenas, malas y blancos deben sumar 15")
        answers = record.get("answers")
        if not isinstance(answers, (list, tuple)) or len(answers) != 15 or any(not isinstance(a, str) or len(a) != 1 or a not in "ABCDE-X" for a in answers):
            raise ValueError("Un registro confirmado necesita 15 respuestas válidas, sin interrogantes")
        if counts[2] != list(answers).count("-"):
            raise ValueError("La cantidad de blancos no coincide con las respuestas")
        doubles = record.get("double", list(answers).count("X"))
        if isinstance(doubles, bool) or not isinstance(doubles, int) or doubles != list(answers).count("X") or not 0 <= doubles <= counts[1]:
            raise ValueError("Las dobles marcas deben estar incluidas entre las malas")
        if record.get("score") is None:
            raise ValueError("Falta el puntaje de un registro confirmado")
        try:
            score = Decimal(_decimal(record["score"]))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("El puntaje no es válido") from exc
        expected = sum(Decimal(value) * weight for value, weight in zip(counts, coeff))
        if abs(score - expected) > Decimal("0.000001"):
            raise ValueError("El puntaje no coincide con la regla de calificación del reporte")
        record["_score"] = score
        record["_time_value"], record["_seconds"] = _time(record.get("time"))
        record["_index"] = index
        grouped[record["grade"]].append(record)
    for group in grouped.values():
        group.sort(key=lambda r: (-r["_score"], r["_seconds"] if r["_seconds"] is not None else math.inf,
                                  r["name"].casefold(), r["_index"]))
        last_key, last_rank = None, None
        for index, record in enumerate(group, 1):
            key = (record["_score"], record["_seconds"])
            if key != last_key:
                last_rank = index
            record["_rank"] = last_rank
            last_key = key
    return grouped, coeff


def _sheet(source: bytes, records: list[dict], coeff) -> bytes:
    doc = _parse(source)
    data = _nodes(doc, "sheetData")[0]
    rows = {int(row.getAttribute("r")): row for row in _nodes(data, "row")}
    if not {1, 2, 3}.issubset(rows):
        raise ValueError("La plantilla no contiene cabecera y filas de resultados esperadas")
    # Excel can serialize distant blank rows (the reference reaches row429).
    # Extend the last actual styled student row, not those empty default rows.
    styled_rows = [number for number, row in rows.items() if number >= 3 and row.getAttribute("ht") == "30"
                   and len(_nodes(row, "c")) >= 9]
    prototype_number = max(styled_rows) if styled_rows else 3
    prototype = rows[prototype_number].cloneNode(deep=True)
    capacity = max(72, len(records) + 2)
    for number in list(rows):
        if number > capacity:
            data.removeChild(rows.pop(number))
    for number in range(3, capacity + 1):
        if number not in rows or not _nodes(rows[number], "c"):
            row = prototype.cloneNode(deep=True)
            row.setAttribute("r", str(number))
            for cell in _nodes(row, "c"):
                col = re.sub(r"\d+", "", cell.getAttribute("r"))
                cell.setAttribute("r", f"{col}{number}")
            if number in rows:
                data.replaceChild(row, rows[number])
            else:
                data.appendChild(row)
            rows[number] = row
        for cell in _nodes(rows[number], "c"):
            _clear(cell)
    header = {re.sub(r"\d+", "", cell.getAttribute("r")): cell for cell in _nodes(rows[2], "c")}
    for col, label in zip("ABCDEFGHI", HEADERS):
        _text(header[col], label)
    for row_number, record in enumerate(records, 3):
        cells = {re.sub(r"\d+", "", cell.getAttribute("r")): cell for cell in _nodes(rows[row_number], "c")}
        for col in "ABCDEFGHI":
            if col not in cells:
                sample = next((n for n in _nodes(prototype, "c") if n.getAttribute("r").startswith(col)), None)
                attrs = {"r": f"{col}{row_number}"}
                if sample is not None and sample.hasAttribute("s"):
                    attrs["s"] = sample.getAttribute("s")
                cell = _element(doc, "c", attrs)
                rows[row_number].appendChild(cell)
                cells[col] = cell
        last = len(records) + 2
        hr = f"$H$3:$H${last}"
        ir = f"$I$3:$I${last}"
        rank_formula = (f'RANK(H{row_number},{hr},0)+IF(I{row_number}="",'
                        f'COUNTIFS({hr},H{row_number},{ir},">=0",{ir},"<>"),'
                        f'COUNTIFS({hr},H{row_number},{ir},"<"&I{row_number},{ir},"<>"))')
        _number(cells["A"], record["_rank"], rank_formula)
        _text(cells["B"], record["name"])
        _text(cells["C"], record["school"])
        _text(cells["D"], record.get("order", ""))
        for col, field in zip("EFG", ("correct", "incorrect", "blank")):
            _number(cells[col], record[field])
        formula = f"E{row_number}*({_decimal(coeff[0])})+F{row_number}*({_decimal(coeff[1])})+G{row_number}*({_decimal(coeff[2])})"
        _number(cells["H"], record["_score"], formula)
        _number(cells["I"], record["_time_value"])
    for dimension in _nodes(doc, "dimension"):
        dimension.setAttribute("ref", f"A1:I{capacity}")
    for view in _nodes(doc, "sheetView"):
        view.setAttribute("view", "normal")
        view.setAttribute("tabSelected", "0")
        view.setAttribute("showGridLines", "0")
    # Native repeating column headers keep multi-page reports readable.
    return _xml(doc)


def _draw_element(doc, prefix, name, attrs=None, value=None):
    return _element(doc, f"{prefix}:{name}", attrs, value, XDR if prefix == "xdr" else DRAW)


def _overlay(doc, x, y, width, height, lines, fill="092E74", size=3000, shape_id=100, cell_anchor=None):
    """Add native vector cover/text; reference bitmap bytes remain untouched."""
    if cell_anchor:
        anchor = _draw_element(doc, "xdr", "twoCellAnchor", {"editAs": "oneCell"})
        first_col, first_offset, last_col, last_offset, top, bottom = cell_anchor
        for name, col, offset, row_offset in (("from", first_col, first_offset, top), ("to", last_col, last_offset, bottom)):
            point = _draw_element(doc, "xdr", name)
            for tag, value in (("col",col),("colOff",offset),("row",0),("rowOff",row_offset)):
                point.appendChild(_draw_element(doc, "xdr", tag, value=value))
            anchor.appendChild(point)
    else:
        anchor = _draw_element(doc, "xdr", "absoluteAnchor")
        anchor.appendChild(_draw_element(doc, "xdr", "pos", {"x": int(x), "y": int(y)}))
        anchor.appendChild(_draw_element(doc, "xdr", "ext", {"cx": int(width), "cy": int(height)}))
    shape = _draw_element(doc, "xdr", "sp")
    nonvisual = _draw_element(doc, "xdr", "nvSpPr")
    nonvisual.appendChild(_draw_element(doc, "xdr", "cNvPr", {"id": shape_id, "name": f"OMR grado {shape_id}"}))
    nonvisual.appendChild(_draw_element(doc, "xdr", "cNvSpPr", {"txBox": "1"}))
    shape.appendChild(nonvisual)
    props = _draw_element(doc, "xdr", "spPr")
    transform = _draw_element(doc, "a", "xfrm")
    transform.appendChild(_draw_element(doc, "a", "off", {"x": int(x), "y": int(y)}))
    transform.appendChild(_draw_element(doc, "a", "ext", {"cx": int(width), "cy": int(height)}))
    props.appendChild(transform)
    geometry = _draw_element(doc, "a", "prstGeom", {"prst": "rect"})
    geometry.appendChild(_draw_element(doc, "a", "avLst"))
    props.appendChild(geometry)
    solid = _draw_element(doc, "a", "solidFill")
    solid.appendChild(_draw_element(doc, "a", "srgbClr", {"val": fill}))
    props.appendChild(solid)
    line = _draw_element(doc, "a", "ln")
    line.appendChild(_draw_element(doc, "a", "noFill"))
    props.appendChild(line)
    shape.appendChild(props)
    body = _draw_element(doc, "xdr", "txBody")
    body.appendChild(_draw_element(doc, "a", "bodyPr", {"anchor": "ctr", "wrap": "square", "lIns": "0", "rIns": "0", "tIns": "0", "bIns": "0"}))
    body.appendChild(_draw_element(doc, "a", "lstStyle"))
    for index, text in enumerate(lines):
        paragraph = _draw_element(doc, "a", "p")
        paragraph.appendChild(_draw_element(doc, "a", "pPr", {"algn": "ctr"}))
        run = _draw_element(doc, "a", "r")
        properties = _draw_element(doc, "a", "rPr", {"lang": "es-PE", "sz": str(size if index == 0 else size * 55 // 100), "b": "1"})
        color = _draw_element(doc, "a", "solidFill")
        color.appendChild(_draw_element(doc, "a", "srgbClr", {"val": "FFFFFF" if fill != "FFFF00" else "092E74"}))
        properties.appendChild(color)
        properties.appendChild(_draw_element(doc, "a", "latin", {"typeface": "Arial"}))
        run.appendChild(properties)
        run.appendChild(_draw_element(doc, "a", "t", value=text))
        paragraph.appendChild(run)
        body.appendChild(paragraph)
    shape.appendChild(body)
    anchor.appendChild(shape)
    anchor.appendChild(_draw_element(doc, "xdr", "clientData"))
    doc.documentElement.appendChild(anchor)


def _drawing(source: bytes, grade: int, course: str) -> bytes:
    doc = _parse(source)
    normalized = str(course).casefold()
    course_title = "COMUNICACIÓN" if "comunic" in normalized else "MATEMÁTICA" if "matem" in normalized else str(course).upper()
    # Update the template's separate place textbox rather than losing it.
    for node in list(doc.getElementsByTagNameNS(DRAW, "t")):
        while node.firstChild:
            node.removeChild(node.firstChild)
        node.appendChild(doc.createTextNode(f"{course_title} - GRADO {grade}"))
    for props in doc.getElementsByTagNameNS(DRAW, "rPr"):
        props.setAttribute("sz", "2200")
    x, y, width, height = 246532, 22412, 12376462, 1476000
    _overlay(doc, x + width * .765, y, width * .202, height, [f"{grade}º GRADO", "RESULTADOS"], shape_id=100+grade,
             cell_anchor=(5,200000,8,711141,22412,1498412))
    if course_title != "MATEMÁTICA":
        _overlay(doc, x + width * .27, y + height * .22, width * .49, height * .59,
                 [course_title], fill="FFFF00", size=5400, shape_id=200+grade,
                 cell_anchor=(1,3500000,5,0,350000,1180000))
    return _xml(doc)


def export_report(records, output_path=None, template_path=None, course="Matematica", scoring=None):
    """Return XLSX bytes, or save atomically and return Path when output_path given.

    Only confirmed records are exported. Confirmed but incomplete/inconsistent
    records raise ValueError. Rows sort by score descending, then known delivery
    time ascending. Equal score/time receives the same competition rank. Missing
    times sort after known times. All six grade sheets are always present.
    """
    grouped, coeff = _validated(records, scoring)
    path = Path(template_path) if template_path else DEFAULT_TEMPLATE
    if not path.is_file():
        raise FileNotFoundError("No se encontró la plantilla de reporte de la editorial")
    with zipfile.ZipFile(path) as original:
        parts = {name: original.read(name) for name in original.namelist()}
    required = ("xl/worksheets/sheet1.xml", "xl/worksheets/_rels/sheet1.xml.rels", "xl/drawings/drawing1.xml", "xl/drawings/_rels/drawing1.xml.rels")
    if any(name not in parts for name in required):
        raise ValueError("La plantilla no tiene el diseño esperado de la editorial")
    sheet_source, sheet_rels, drawing_source, drawing_rels = (parts[name] for name in required)
    book = _parse(parts["xl/workbook.xml"])
    sheet_list = _nodes(book, "sheets")[0]
    for node in list(sheet_list.childNodes):
        sheet_list.removeChild(node)
    names = _nodes(book, "definedNames")
    defined = names[0] if names else _element(book, "definedNames")
    if not names:
        calc = _nodes(book, "calcPr")
        book.documentElement.insertBefore(defined, calc[0] if calc else None)
    for node in list(defined.childNodes):
        defined.removeChild(node)
    # Remove stale original machine paths and coauthor revision references.
    for node in list(book.documentElement.childNodes):
        if node.nodeType == node.ELEMENT_NODE and node.localName in ("AlternateContent", "revisionPtr"):
            book.documentElement.removeChild(node)
    for calc in _nodes(book, "calcPr"):
        calc.setAttribute("calcMode", "auto")
        calc.setAttribute("fullCalcOnLoad", "1")
        calc.setAttribute("forceFullCalc", "1")
    rels = _parse(parts["xl/_rels/workbook.xml.rels"])
    for node in list(rels.documentElement.childNodes):
        if node.nodeType == node.ELEMENT_NODE and any(node.getAttribute("Type").endswith("/"+kind) for kind in ("worksheet", "calcChain", "sharedStrings")):
            rels.documentElement.removeChild(node)
    types = _parse(parts["[Content_Types].xml"])
    for node in list(types.documentElement.childNodes):
        if node.nodeType == node.ELEMENT_NODE and node.getAttribute("PartName") in ("/xl/calcChain.xml", "/xl/sharedStrings.xml"):
            types.documentElement.removeChild(node)
    for grade in range(1, 7):
        title = f"Grado {grade}"
        sheet = _element(book, "sheet", {"name": title, "sheetId": grade})
        sheet.setAttributeNS(REL, "r:id", f"rIdGrado{grade}")
        sheet_list.appendChild(sheet)
        last = max(3, len(grouped[grade])+2)
        for name, value in (("_xlnm.Print_Area", f"'{title}'!$A$1:$I${last}"), ("_xlnm.Print_Titles", f"'{title}'!$1:$2")):
            defined.appendChild(_element(book, "definedName", {"name": name, "localSheetId": grade-1}, value))
        rels.documentElement.appendChild(_element(rels, "Relationship", {"Id": f"rIdGrado{grade}", "Type": REL+"/worksheet", "Target": f"worksheets/sheet{grade}.xml"}, namespace=PKG))
        parts[f"xl/worksheets/sheet{grade}.xml"] = _sheet(sheet_source, grouped[grade], coeff)
        relationships = _parse(sheet_rels)
        for rel in relationships.documentElement.childNodes:
            if rel.nodeType == rel.ELEMENT_NODE and rel.getAttribute("Type").endswith("/drawing"):
                rel.setAttribute("Target", f"../drawings/drawing{grade}.xml")
        parts[f"xl/worksheets/_rels/sheet{grade}.xml.rels"] = _xml(relationships)
        parts[f"xl/drawings/drawing{grade}.xml"] = _drawing(drawing_source, grade, course)
        parts[f"xl/drawings/_rels/drawing{grade}.xml.rels"] = drawing_rels
        if grade > 1:
            for part_name, content_type in ((f"/xl/worksheets/sheet{grade}.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"), (f"/xl/drawings/drawing{grade}.xml", "application/vnd.openxmlformats-officedocument.drawing+xml")):
                types.documentElement.appendChild(_element(types, "Override", {"PartName": part_name, "ContentType": content_type}, namespace=CT))
    parts["xl/workbook.xml"] = _xml(book)
    parts["xl/_rels/workbook.xml.rels"] = _xml(rels)
    parts["[Content_Types].xml"] = _xml(types)
    parts.pop("xl/calcChain.xml", None)
    parts.pop("xl/sharedStrings.xml", None)
    # Office application metadata must not retain the old one-sheet title list.
    app_props = _parse(parts["docProps/app.xml"])
    for node in list(app_props.documentElement.childNodes):
        if node.nodeType == node.ELEMENT_NODE and node.localName in ("HeadingPairs", "TitlesOfParts"):
            app_props.documentElement.removeChild(node)
    parts["docProps/app.xml"] = _xml(app_props)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as destination:
        for name, data in parts.items():
            destination.writestr(name, data)
    data = buffer.getvalue()
    if output_path is None:
        return data
    output = Path(output_path)
    if output.resolve() == path.resolve():
        raise ValueError("El reporte no puede sobrescribir la plantilla original")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=output.name+".", suffix=".tmp", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
