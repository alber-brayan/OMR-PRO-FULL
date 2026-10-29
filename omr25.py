"""OMR 2.5: ficha A4 horizontal y lectura de marcas, sin OCR.

La misma geometría en milímetros gobierna el PDF, la lectura y las anotaciones.
Los campos manuscritos de orden/hora no se interpretan automáticamente.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from itertools import combinations
from typing import Any

import cv2
import numpy as np
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PROFILE_ID = "OMR25-A4H-37-12-15-G6-2026.1"
READER_REVISION = "2.5.3-sector-guides"
LETTERS = list("ABCDEFGHIJKLMNÑOPQRSTUVWXYZ") + ["_"]
OPTIONS = list("ABCDE")
PAGE_W_MM, PAGE_H_MM = 297.0, 210.0
# Aprox. 300 dpi, sin degradar las burbujas de 3 mm a un canvas de 800 px.
CANVAS_W, CANVAS_H = 3508, 2480
SX, SY = CANVAS_W / PAGE_W_MM, CANVAS_H / PAGE_H_MM
INK = HexColor("#182f43")
TEAL = HexColor("#136e68")
MUTED = HexColor("#586570")
BORDER = HexColor("#a8b4bd")


def sheet_geometry() -> dict[str, Any]:
    """Coordenadas con origen arriba-izquierda; una columna por carácter."""
    return {
        "profile_id": PROFILE_ID,
        "page_size_mm": [PAGE_W_MM, PAGE_H_MM],
        "canvas_size": [CANVAS_W, CANVAS_H],
        "letters": list(LETTERS), "options": list(OPTIONS),
        "name_columns": [[(20.2 + col * 4.1, 53.0 + row * 5.0)
                          for row in range(28)] for col in range(37)],
        "school_columns": [[(183.2 + col * 4.1, 53.0 + row * 5.0)
                            for row in range(28)] for col in range(12)],
        "grade_centers": [(241.0 + i * 7.5, 43.0) for i in range(6)],
        "answer_rows": [[(248.0 + i * 8.0, 62.0 + q * 5.65)
                         for i in range(5)] for q in range(15)],
        "anchors": {
            "TL": {"center": [7., 7.], "kind": "solid", "size": 6.},
            "TR": {"center": [290., 7.], "kind": "frame", "size": 6.},
            "BR": {"center": [290., 203.], "kind": "ell", "size": 6.},
            "BL": {"center": [7., 203.], "kind": "circle", "size": 6.},
        },
        "local_guides": [[7., 105.], [290., 105.]],
        "sector_guides": {
            "name": [[18.2,50.], [169.8,50.], [18.2,191.], [169.8,191.]],
            "school": [[181.2,50.], [230.3,50.], [181.2,191.], [230.3,191.]],
            "grade": [[238.2,38.5], [282.6,38.5], [238.2,47.5], [282.6,47.5]],
            "answers": [[238.,60.], [283.5,60.], [238.,145.], [283.5,145.]],
        },
        "letter_diameter_mm": 3.0, "grade_diameter_mm": 3.8,
        "answer_diameter_mm": 4.5,
        "result_boxes_mm": [[240., 179.1, 12., 8.], [254.5, 179.1, 12., 8.],
                            [269., 179.1, 12., 8.]],
        "manual_boxes_mm": {"order": [240., 161.3, 18., 7.],
                            "time": [262., 161.3, 19., 7.]},
    }


def _px(point):
    return (float(point[0]) * SX, float(point[1]) * SY)


def build_sheet_pdf(curso="Matematica", settings=None, base_dir=None):
    """PDF vectorial A4H. Logos se encajan sin deformación en espacios 30:9."""
    cfg = settings or {}
    brand = cfg.get("branding") or {}
    title = str(brand.get("title") or "CONCURSO FERMAT")[:60]
    course = "COMUNICACIÓN" if "com" in str(curso).lower() else "MATEMÁTICA"
    base = Path(base_dir or Path(__file__).resolve().parent)
    g = sheet_geometry()
    buf = io.BytesIO()
    width, height = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(width, height), pageCompression=1)
    c.setTitle(f"Ficha OMR 2.5 A4 horizontal - {course}")
    c.setAuthor("OMR OCR SCAN")
    c.setSubject(PROFILE_ID)

    def text(x, y, s, size=7., bold=False, color=INK, align="left"):
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        draw = c.drawCentredString if align == "center" else c.drawRightString if align == "right" else c.drawString
        draw(x * mm, height - y * mm, str(s))

    def rect(x, y, w, h, color=BORDER, fill=None, radius=0.8, weight=.5):
        c.setStrokeColor(color); c.setLineWidth(weight)
        if fill is not None: c.setFillColor(fill)
        c.roundRect(x * mm, height - (y + h) * mm, w * mm, h * mm,
                    radius * mm, stroke=1, fill=int(fill is not None))

    def line(x1, y1, x2, y2, color=BORDER, weight=.5):
        c.setStrokeColor(color); c.setLineWidth(weight)
        c.line(x1 * mm, height-y1*mm, x2*mm, height-y2*mm)

    def bubble(point, diameter, label=None):
        c.setStrokeColor(HexColor("#222222")); c.setLineWidth(.5 if diameter == 3 else .65)
        c.setFillColor(white)
        c.circle(point[0]*mm, height-point[1]*mm, diameter*mm/2, stroke=1, fill=1)
        if label is not None:
            # Tinta tenue: orientación humana, por debajo del umbral de relleno.
            text(point[0], point[1]+.55, label, 4.7 if diameter == 3 else 6.2,
                 color=HexColor("#e0e0e0"), align="center")

    def anchor(x, y, size, kind):
        h = size / 2
        c.setFillColor(HexColor("#111111"))
        if kind == "circle":
            c.circle(x*mm, height-y*mm, h*mm, stroke=0, fill=1)
        elif kind == "ell":
            t = size*.38
            c.rect((x+h-t)*mm, height-(y+h)*mm, t*mm, size*mm, stroke=0, fill=1)
            c.rect((x-h)*mm, height-(y+h)*mm, size*mm, t*mm, stroke=0, fill=1)
        else:
            c.rect((x-h)*mm, height-(y+h)*mm, size*mm, size*mm, stroke=0, fill=1)
            if kind == "frame":
                inner = size*.4; c.setFillColor(white)
                c.rect((x-inner/2)*mm, height-(y+inner/2)*mm, inner*mm, inner*mm, stroke=0, fill=1)

    for a in g["anchors"].values(): anchor(*a["center"], a["size"], a["kind"])
    for x, y in g["local_guides"]: anchor(x, y, 3., "solid")

    logos = brand.get("logos") or [brand.get("logo", ""), brand.get("logo_right", "")]
    for i, x in enumerate((13., 234.)):
        path = str(logos[i] or "") if i < len(logos) else ""
        candidate = Path(path) if path else None
        if candidate and not candidate.is_absolute(): candidate = base / candidate
        if candidate and candidate.is_file():
            from reportlab.lib.utils import ImageReader
            im = ImageReader(str(candidate)); iw, ih = im.getSize()
            scale = min(50*mm/iw, 15*mm/ih)
            c.drawImage(im, x*mm+(50*mm-iw*scale)/2,
                        height-24*mm+(15*mm-ih*scale)/2,
                        width=iw*scale, height=ih*scale, mask="auto")
        elif cfg.get("show_logo_placeholders", False):
            rect(x, 9, 50, 15)
            text(x+25, 16, "LOGO / IMAGEN 30:9", 7, True, MUTED, "center")

    # El título configurable se adapta dentro de la franja central.
    font = 16.
    while c.stringWidth(title, "Helvetica-Bold", font) > 158*mm and font > 8: font -= .5
    text(148.5, 14.4, title, font, True, align="center")
    text(148.5, 20, f"{course} / FICHA OMR 2.5", 9, True, TEAL, "center")
    text(148.5, 24.4, "Escriba de izquierda a derecha y marque una letra en cada columna.", 7, color=MUTED, align="center")
    line(12, 27.5, 285, 27.5, TEAL, 1)

    for x, w, name, columns in ((12, 159, "01 / APELLIDOS Y NOMBRES", g["name_columns"]),
                               (175, 57, "02 / COLEGIO", g["school_columns"])):
        rect(x,31,w,165, radius=1.5)
        text(x+3,36.2,name,9,True)
        text(x+w-3,36.2,f"{len(columns)} CAR.",6.5,True,MUTED,"right")
        text(x+3.2,41.8,"POS.",5.1,True,MUTED,"center")
        left = columns[0][0][0]-2.05
        right = left+len(columns)*4.1
        for row in range(28):
            y = columns[0][row][1]
            if row % 2 == 0:
                c.setFillColor(HexColor("#f4f6f7"))
                c.rect(left*mm,height-(y+2.5)*mm,(right-left)*mm,5*mm,stroke=0,fill=1)
            if row:
                line(left,y-2.5,right,y-2.5,HexColor("#e0e5e8"),.25)
        rect(left,43.4,len(columns)*4.1,4.5,radius=0,weight=.5)
        for pos, col in enumerate(columns):
            text(col[0][0],41.8,f"{pos+1:02d}",6.3,True,MUTED,"center")
            if pos: line(left+pos*4.1,43.4,left+pos*4.1,47.9,weight=.35)
            for row, point in enumerate(col): bubble(point,g["letter_diameter_mm"],LETTERS[row])
        for boundary in range(5,len(columns),5):
            line(left+boundary*4.1,43.4,left+boundary*4.1,190.5,HexColor("#9ba8af"),.45)
        for row, letter in enumerate(LETTERS):
            text(x+3.2,columns[0][row][1]+.85,letter,7.6,True,align="center")

    rect(236,31,49,19,radius=1.5)
    text(239,36.2,"03 / GRADO",8.5,True)
    text(282,36.2,"Una marca",6.3,color=MUTED,align="right")
    for i, point in enumerate(g["grade_centers"]):
        text(point[0],40.0,str(i+1),7,True,align="center")
        bubble(point,g["grade_diameter_mm"],str(i+1))
    rect(236,53,49,95,radius=1.5)
    text(239,57.0,"04 / RESPUESTAS",8.2,True)
    for i, opt in enumerate(OPTIONS): text(248+i*8,59.5,opt,5.8,True,align="center")
    for q, row in enumerate(g["answer_rows"]):
        text(243,row[0][1]+.85,f"{q+1:02d}",7.5,True,align="right")
        if q and q % 5 == 0:
            line(240,row[0][1]-2.825,282,row[0][1]-2.825,HexColor("#bcc6cc"),.45)
        for i, point in enumerate(row): bubble(point,g["answer_diameter_mm"],OPTIONS[i])

    for points in g["sector_guides"].values():
        for x,y in points: anchor(x,y,1.2,"solid")

    rect(236,151,49,45,radius=1.4)
    text(239,155.7,"CONTROL Y RESULTADOS",7.2,True)
    for label, key in (("Nº ORDEN","order"),("HORA","time")):
        box=g["manual_boxes_mm"][key]
        text(box[0],159.5,label,6.6,True,MUTED)
        rect(*box,radius=.4,weight=.55)
    text(271.5,166.1,"HH:MM",7.5,color=HexColor("#b5bec4"),align="center")
    line(239,172,282,172)
    for label, box in zip(("BUENAS","MALAS","BLANCOS"),g["result_boxes_mm"]):
        text(box[0]+6,177,label,6.1,True,MUTED,"center"); rect(*box,radius=.4,weight=.55)
    text(260.5,192,"Orden y hora: escritura manual.",5.9,color=MUTED,align="center")
    text(148.5,201,'Columna vacía = espacio; tres vacías seguidas = fin. Rellene sobre la letra. No cubra las guías.',6.5,align="center")
    text(148.5,205,f"A4 horizontal / 100% / Edición 2.5.3 / {PROFILE_ID}",6,color=MUTED,align="center")
    c.showPage(); c.save()
    slug = "comunicacion" if course.startswith("COM") else "matematica"
    return buf.getvalue(), f"ficha_{slug}_omr25_a4_horizontal.pdf"


def _ordered_quad(points):
    points = np.asarray(points, dtype=np.float32)
    center = points.mean(axis=0)
    angles = np.arctan2(points[:,1]-center[1],points[:,0]-center[0])
    ordered = points[np.argsort(angles)]
    return np.roll(ordered, -int(np.argmin(ordered.sum(axis=1))), axis=0)


def _candidate_quads(gray):
    """Detecta figuras de tinta compactas; excluye los anillos de burbujas."""
    # El umbral local separa las guías también cuando el escritorio o una
    # sombra oscurecen un lado de la foto; Otsu global puede unirlos al fondo.
    binary = cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV,101,15)
    contours,_=cv2.findContours(binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    area=gray.size; candidates=[]
    for contour in contours:
        a=cv2.contourArea(contour)
        if a < area*.00018 or a > area*.018: continue
        x,y,w,h=cv2.boundingRect(contour)
        if not .42 < w/max(h,1) < 2.4: continue
        roi=binary[y:y+h,x:x+w]
        fill=float(np.count_nonzero(roi))/max(w*h,1)
        if fill < .40 or a/(w*h) < .40: continue
        moments=cv2.moments(contour)
        # Centro del bounding box, no centro de masa: la L es asimétrica.
        candidates.append(((x+w/2,y+h/2),a))
    candidates=sorted(candidates,key=lambda item:item[1],reverse=True)[:20]
    if len(candidates)<4:
        raise ValueError("No se detectan las cuatro guías de esquina. Capture la ficha completa, enfocada y sin sombras fuertes.")
    # Máxima área evita elegir guías laterales o dibujos interiores como esquinas.
    quads=[]
    for comb in combinations(candidates,4):
        pts=np.array([item[0] for item in comb],np.float32)
        hull=cv2.convexHull(pts)
        if len(hull)!=4:continue
        hull_area=cv2.contourArea(hull)
        if hull_area < area*.16:continue
        quads.append((hull_area,_ordered_quad(pts)))
    if not quads:raise ValueError("Las guías no forman una ficha completa. Aleje un poco la cámara y evite recortar los bordes.")
    quads.sort(key=lambda item:item[0],reverse=True)
    return [quad for _,quad in quads[:4]]


def _marker_score(gray, point, kind, scale_x, scale_y):
    """Compara forma y orientación de cada guía contra su diseño vectorial."""
    cx=int(round(point[0]*scale_x));cy=int(round(point[1]*scale_y))
    half_x=int(round(2.75*scale_x));half_y=int(round(2.75*scale_y))
    patch=gray[max(0,cy-half_y):cy+half_y+1,max(0,cx-half_x):cx+half_x+1]
    if patch.size==0:return -1.
    p=cv2.resize(patch,(41,41),interpolation=cv2.INTER_AREA)
    observed=p<130
    expected=np.ones((41,41),bool)
    if kind=="frame":expected[12:29,12:29]=False
    elif kind=="circle":
        yy,xx=np.ogrid[:41,:41];expected=(xx-20)**2+(yy-20)**2<=22**2
    elif kind=="ell":expected[:25,:25]=False
    return float(np.mean(expected==observed))


def _rectify(image):
    height,width=image.shape[:2]
    if min(height,width)<500:raise ValueError("Imagen demasiado pequeña para leer nombres. Use el original de la cámara o un escaneo de 300 dpi.")
    reduction=min(1.,2000/max(height,width))
    work=cv2.resize(image,None,fx=reduction,fy=reduction,interpolation=cv2.INTER_AREA) if reduction<1 else image
    gray=cv2.cvtColor(work,cv2.COLOR_BGR2GRAY)
    quads=_candidate_quads(gray)
    anchors=sheet_geometry()["anchors"]
    preview_size=(1754,1240)
    psx,psy=preview_size[0]/297.,preview_size[1]/210.
    destination=np.array([(7*psx,7*psy),(290*psx,7*psy),(290*psx,203*psy),(7*psx,203*psy)],np.float32)
    best=None
    for quad in quads:
        for roll in range(4):
            src=np.roll(quad,-roll,axis=0)
            matrix=cv2.getPerspectiveTransform(src,destination)
            preview=cv2.warpPerspective(gray,matrix,preview_size,borderValue=255)
            scores=[_marker_score(preview,a["center"],a["kind"],psx,psy) for a in anchors.values()]
            score=float(np.mean(scores))
            if best is None or score>best[0]:best=(score,src,scores)
    if best is None or best[0]<.76 or min(best[2])<.61:
        raise ValueError("Las guías no coinciden con una ficha OMR 2.5 A4 horizontal. Use la plantilla 2.5 y capture las cuatro esquinas completas.")
    source=best[1]/reduction
    ppmm_x=(np.linalg.norm(source[1]-source[0])+np.linalg.norm(source[2]-source[3]))/(2*283.)
    ppmm_y=(np.linalg.norm(source[3]-source[0])+np.linalg.norm(source[2]-source[1]))/(2*196.)
    pixels_per_mm=float(min(ppmm_x,ppmm_y))
    if pixels_per_mm<4.:
        raise ValueError("Resolución insuficiente para las burbujas de nombres. Envíe la fotografía original, sin reducirla, o escanee a 300 dpi.")
    dst=np.array([_px(a["center"]) for a in anchors.values()],np.float32)
    matrix=cv2.getPerspectiveTransform(source,dst)
    warped=cv2.warpPerspective(image,matrix,(CANVAS_W,CANVAS_H),flags=cv2.INTER_CUBIC,borderValue=(255,255,255))
    return warped,{"alignment_score":round(best[0],4),"corner_scores":[round(v,4) for v in best[2]],
                   "source_width":width,"source_height":height,"source_pixels_per_mm":round(pixels_per_mm,3),
                   "profile_id":PROFILE_ID}


def _measure(gray, centers, diameter):
    """Muestrea el núcleo evitando el contorno impreso; fondo local por burbuja."""
    radius=diameter*SX/2
    inner=max(4.,radius*.58)
    outer=radius*1.36
    scores=[]
    for point in centers:
        cx,cy=_px(point)
        # Pequeña tolerancia local; no puede alcanzar el centro de la vecina.
        best=0.
        for dx,dy in ((0,0),(-2,0),(2,0),(0,-2),(0,2)):
            x=int(round(cx+dx));y=int(round(cy+dy));side=int(math.ceil(outer))
            crop=gray[y-side:y+side+1,x-side:x+side+1]
            if crop.shape!=(2*side+1,2*side+1):continue
            yy,xx=np.ogrid[-side:side+1,-side:side+1]
            distance=xx*xx+yy*yy
            core=crop[distance<=inner*inner]
            annulus=crop[(distance>=(radius*1.13)**2)&(distance<=outer*outer)]
            background=float(np.percentile(annulus,80)) if len(annulus) else 255.
            # Adapta a sombra suave sin confundir el anillo con una marca.
            threshold=min(185.,background-35.)
            dark=float(np.mean(core<threshold))
            contrast=max(0.,background-float(np.mean(core)))/max(background,1.)
            score=dark*.4+contrast*.6
            best=max(best,score)
        scores.append(round(float(best),4))
    return scores


def _selection(scores):
    """Prefiere un relleno dominante; conserva dobles casi iguales y dudas."""
    strong=[i for i,s in enumerate(scores) if s>=.36]
    weak=[i for i,s in enumerate(scores) if .055<=s<.36]
    ranked=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
    if strong:
        first=ranked[0]
        second=scores[ranked[1]] if len(ranked)>1 else 0.
        # Diferencia absoluta y relativa: una variación mínima por ruido no
        # decide entre dos rellenos similares. No interviene la clave.
        dominant=scores[first]-second>=.13 and second<=scores[first]*.85
        if second<.09 or dominant:return "single",[first]
    if len(strong)>1:return "double",strong
    if len(strong)==1:
        return "uncertain",strong+weak
    if weak:return "uncertain",weak
    return "blank",[]


def _note_dominance(scores, state, selected, label, options, decisions):
    if decisions is None or state!="single":return
    if any(i!=selected[0] and score>=.055 for i,score in enumerate(scores)):
        choice=options[selected[0]].replace("_","espacio")
        decisions.append(f"{label}: se eligió {choice} por mayor relleno; otras marcas más tenues se descartaron.")


def _read_text(gray, columns, label, issues, decisions=None):
    chars=[]; states=[]; confidence=[]; selected_rows=[]
    empty_run=0
    for index,centers in enumerate(columns):
        scores=_measure(gray,centers,3.)
        state,selected=_selection(scores)
        selected_rows.append(selected)
        states.append(state)
        confidence.append(round(max(scores),4))
        _note_dominance(scores,state,selected,f"{label}, posición {index+1}",LETTERS,decisions)
        if state=="single":chars.append(LETTERS[selected[0]].replace("_"," "))
        elif state=="blank":chars.append(" ")
        else:
            chars.append("?")
            issues.append(f"{label}: posición {index+1} con {'más de una marca' if state=='double' else 'marca tenue o borrado'}.")
        empty_run=empty_run+1 if state=="blank" else 0
        if empty_run==3:
            remaining=len(columns)-index-1
            states.extend(["after_end"]*remaining)
            confidence.extend([0.]*remaining)
            selected_rows.extend([[] for _ in range(remaining)])
            break
    text="".join(chars).strip()
    if not text:
        issues.append(f"{label}: no se detectaron letras.")
    return text,states,confidence,selected_rows


def read_sheet(image_path_or_np_array):
    """Lee el original de cámara/escáner. Devuelve dudas explícitas y el warp."""
    if isinstance(image_path_or_np_array,(str,Path)):
        from PIL import Image,ImageOps
        try:
            with Image.open(image_path_or_np_array) as im:
                image=cv2.cvtColor(np.asarray(ImageOps.exif_transpose(im).convert("RGB")),cv2.COLOR_RGB2BGR)
        except Exception as exc:raise ValueError("No se pudo abrir la fotografía.") from exc
    else:
        image=np.asarray(image_path_or_np_array)
        if image.ndim==2:image=cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
        elif image.ndim==3 and image.shape[2]==4:image=cv2.cvtColor(image,cv2.COLOR_BGRA2BGR)
    if image.ndim!=3 or image.shape[2]!=3:raise ValueError("Formato de imagen no compatible.")
    warped,quality=_rectify(image)
    gray=cv2.cvtColor(warped,cv2.COLOR_BGR2GRAY)
    g=sheet_geometry();issues=[]
    crop=gray[int(50*SY):int(192*SY),int(18*SX):int(230*SX)]
    sharpness=float(cv2.Laplacian(crop,cv2.CV_64F).var())
    quality["sharpness"]=round(sharpness,2)
    quality["paper_brightness"]=round(float(np.percentile(crop,80)),2)
    if sharpness<30:issues.append("La imagen está desenfocada; vuelva a capturar la ficha.")
    if quality["paper_brightness"]<115:issues.append("La ficha está demasiado oscura; mejore la iluminación.")
    guides=[];guide_offsets=[]
    for x,y in g["local_guides"]:
        px,py=map(lambda v:int(round(v)),_px((x,y)))
        side=int(1.0*SX)
        guides.append(float(np.mean(gray[py-side:py+side+1,px-side:px+side+1]<140)))
        search=int(3.*SX)
        patch=gray[py-search:py+search+1,px-search:px+search+1]
        threshold=min(160.,float(np.percentile(patch,80))-45.)
        yy,xx=np.where(patch<threshold)
        if len(xx)>=int((3*SX)**2*.20):
            offset=[round((float(xx.mean())-search)/SX,3),
                    round((float(yy.mean())-search)/SY,3)]
            guide_offsets.append(offset)
        else:guide_offsets.append(None)
    quality["side_guide_scores"]=[round(v,4) for v in guides]
    quality["side_guide_offsets_mm"]=guide_offsets
    if min(guides)<.55:issues.append("Una guía lateral está oculta o desalineada; revise la foto antes de confirmar.")
    elif any(offset is None or math.hypot(*offset)>.4 for offset in guide_offsets):
        issues.append("Las guías laterales indican deformación local de la hoja. Aplane la ficha y vuelva a fotografiarla.")
    # Cada retícula se registra por sus anillos impresos. La lectura no depende
    # de la clave ni del contenido esperado de nombres/colegios.
    from omr_regions import register_geometry
    g, region_tracking = register_geometry(gray, g, SX, SY)
    quality["region_tracking"] = region_tracking
    for region, label in (("name", "nombres"), ("school", "colegio"), ("grade", "grado"), ("answers", "respuestas")):
        if not region_tracking[region]["reliable"]:
            issues.append(f"No se pudo verificar la cuadrícula de {label}; revise esa zona o vuelva a fotografiarla.")
    decisions=[]
    name,name_states,name_conf,name_selected=_read_text(gray,g["name_columns"],"Nombre",issues,decisions)
    school,school_states,school_conf,school_selected=_read_text(gray,g["school_columns"],"Colegio",issues,decisions)
    grade_scores=_measure(gray,g["grade_centers"],g["grade_diameter_mm"])
    state,selected=_selection(grade_scores)
    _note_dominance(grade_scores,state,selected,"Grado",list("123456"),decisions)
    grade=selected[0]+1 if state=="single" else None
    if grade is None:issues.append("Grado: marque exactamente una burbuja del 1 al 6.")
    grade_selected=selected
    answers=[];answer_states=[];answer_conf=[];answer_selected=[]
    for index,centers in enumerate(g["answer_rows"]):
        scores=_measure(gray,centers,g["answer_diameter_mm"])
        state,selected=_selection(scores)
        _note_dominance(scores,state,selected,f"Pregunta {index+1}",OPTIONS,decisions)
        answer_selected.append(selected)
        answer_states.append(state);answer_conf.append(round(max(scores),4))
        if state=="single":answers.append(OPTIONS[selected[0]])
        elif state=="double":
            answers.append("X")
            issues.append(f"Pregunta {index+1}: dos rellenos casi iguales; revise cuál corresponde.")
        elif state=="blank":answers.append("-")
        else:
            answers.append("?")
            issues.append(f"Pregunta {index+1}: marca tenue o borrado; confirme la respuesta.")
    return {"profile_id":PROFILE_ID,"reader_revision":READER_REVISION,
            "sampling_geometry":g,"name":name,"school":school,"grade":grade,
            "answers":answers,"issues":issues,"decisions":decisions,"needs_review":bool(issues),"quality":quality,
            "name_states":name_states,"school_states":school_states,
            "answer_states":answer_states,"name_mark_scores":name_conf,
            "school_mark_scores":school_conf,"answer_mark_scores":answer_conf,
            "grade_mark_scores":grade_scores,"name_selected_rows":name_selected,
            "school_selected_rows":school_selected,"grade_selected_indices":grade_selected,
            "answer_selected_indices":answer_selected,"warped_image":warped}


def grade_answers(answers,key,scoring=None):
    """Califica 15 respuestas. Dobles = malas; '?' obliga a revisar."""
    answers=[str(a).upper() for a in answers]
    if isinstance(key,str):key=list(key.replace(" ","").upper())
    key=[str(k).upper() for k in key]
    if len(answers)!=15 or len(key)!=15 or any(k not in OPTIONS for k in key):
        raise ValueError("Se necesitan 15 respuestas y una clave de 15 alternativas A-E.")
    if any(a not in OPTIONS+["-","X"] for a in answers):
        raise ValueError("Hay respuestas dudosas; revíselas antes de calificar.")
    scoring=scoring or {}
    good=float(scoring.get("correct",scoring.get("buenas",10)))
    bad=float(scoring.get("incorrect",scoring.get("malas",-1)))
    empty=float(scoring.get("blank",scoring.get("blanco",0)))
    if not all(math.isfinite(value) for value in (good,bad,empty)):
        raise ValueError("Los puntajes deben ser números finitos.")
    correct=incorrect=blank=double=0;results=[]
    for q,(answer,expected) in enumerate(zip(answers,key),1):
        if answer=="-":blank+=1;status="blank"
        elif answer==expected:correct+=1;status="correct"
        else:
            incorrect+=1;status="double" if answer=="X" else "incorrect"
            if answer=="X":double+=1
        results.append({"question":q,"answer":answer,"expected":expected,"status":status})
    return {"correct":correct,"incorrect":incorrect,"blank":blank,"double":double,
            "score":round(correct*good+incorrect*bad+blank*empty,4),"results":results}


def annotate_reading(reading,grading=None):
    """Resultado visual: anillos por respuesta y resumen en su caja reservada."""
    image=reading["warped_image"].copy()
    g=reading.get("sampling_geometry") or sheet_geometry()
    colors={"correct":(45,145,35),"incorrect":(40,40,210),"double":(25,125,230),"blank":(170,110,35)}
    for prefix in ("name","school"):
        for col, selected in enumerate(reading.get(f"{prefix}_selected_rows",[])):
            if col>=len(g[f"{prefix}_columns"]):break
            states=reading.get(f"{prefix}_states",[])
            good=col<len(states) and states[col]=="single"
            color=(45,145,35) if good else (25,125,230)
            for row in selected:
                center=tuple(int(round(v)) for v in _px(g[f"{prefix}_columns"][col][row]))
                cv2.circle(image,center,int(1.65*SX),color,3,cv2.LINE_AA)
    for selected in reading.get("grade_selected_indices",[]):
        center=tuple(int(round(v)) for v in _px(g["grade_centers"][selected]))
        cv2.circle(image,center,int(2.1*SX),(45,145,35) if reading.get("grade") else (25,125,230),3,cv2.LINE_AA)
    results=grading.get("results",[]) if grading else []
    for q,answer in enumerate(reading.get("answers",[])):
        if q>=15:break
        status=results[q]["status"] if q<len(results) else "blank"
        color=colors.get(status,(120,70,30))
        if answer in OPTIONS:
            center=tuple(int(round(v)) for v in _px(g["answer_rows"][q][OPTIONS.index(answer)]))
            cv2.circle(image,center,int(2.5*SX),color,4,cv2.LINE_AA)
        elif answer=="X":
            selected=reading.get("answer_selected_indices",[])
            for index in selected[q] if q<len(selected) else []:
                point=tuple(int(round(v)) for v in _px(g["answer_rows"][q][index]))
                cv2.circle(image,point,int(2.5*SX),color,4,cv2.LINE_AA)
            center=tuple(int(round(v)) for v in _px((240.,g["answer_rows"][q][0][1])))
            cv2.putText(image,"X",(center[0],center[1]+8),cv2.FONT_HERSHEY_SIMPLEX,.8,color,2,cv2.LINE_AA)
        elif answer=="?":
            center=tuple(int(round(v)) for v in _px((240.,g["answer_rows"][q][0][1])))
            cv2.putText(image,"?",(center[0],center[1]+8),cv2.FONT_HERSHEY_SIMPLEX,.8,(0,100,220),2,cv2.LINE_AA)
    if grading:
        for box,value in zip(g["result_boxes_mm"],(grading["correct"],grading["incorrect"],grading["blank"])):
            x,y,w,h=box
            x1,y1=map(int,_px((x+.4,y+.4)));x2,y2=map(int,_px((x+w-.4,y+h-.4)))
            cv2.rectangle(image,(x1,y1),(x2,y2),(255,255,255),-1)
            text=str(value);scale=1.0
            size,_=cv2.getTextSize(text,cv2.FONT_HERSHEY_SIMPLEX,scale,2)
            cx,cy=_px((x+w/2,y+h/2))
            cv2.putText(image,text,(int(cx-size[0]/2),int(cy+size[1]/2)),cv2.FONT_HERSHEY_SIMPLEX,scale,(35,75,30),2,cv2.LINE_AA)
    for field,box in g["manual_boxes_mm"].items():
        if reading.get(field) is None or not str(reading[field]).strip():continue
        value=str(reading[field]).strip()[:12]
        x,y,w,h=box
        x1,y1=map(int,_px((x+.4,y+.4)));x2,y2=map(int,_px((x+w-.4,y+h-.4)))
        cv2.rectangle(image,(x1,y1),(x2,y2),(255,255,255),-1)
        font_scale=.72
        size,_=cv2.getTextSize(value,cv2.FONT_HERSHEY_SIMPLEX,font_scale,2)
        if size[0]>(w-.8)*SX:
            font_scale*=((w-.8)*SX)/size[0]
            size,_=cv2.getTextSize(value,cv2.FONT_HERSHEY_SIMPLEX,font_scale,2)
        cx,cy=_px((x+w/2,y+h/2))
        cv2.putText(image,value,(int(cx-size[0]/2),int(cy+size[1]/2)),cv2.FONT_HERSHEY_SIMPLEX,font_scale,(30,30,30),2,cv2.LINE_AA)
    return image
