"""Pruebas sobre el PDF impreso, no sobre una retícula distinta del generador."""
import unittest
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from reportlab.lib.units import mm

from omr25 import (build_sheet_pdf,sheet_geometry,read_sheet,grade_answers,
                   annotate_reading,LETTERS,CANVAS_W,CANVAS_H)


def render_sheet(name="MUÑOZ ANA",school="SAN JOSE",grade=3,answers=None,
                 extra_marks=None,mark_gray=.12,offset_mm=(0,0),dpi=220):
    """Pinta marcas en el PDF vectorial y después simula una cámara."""
    data,_=build_sheet_pdf(settings={"branding":{"title":"CONCURSO FERMAT"}})
    doc=pymupdf.open(stream=data,filetype="pdf")
    page=doc[0];g=sheet_geometry()

    def mark(point,diameter,gray=None,offset=(0,0)):
        point=((point[0]+offset[0])*mm,(point[1]+offset[1])*mm)
        shade=mark_gray if gray is None else gray
        page.draw_circle(point,diameter/2*.83*mm,color=None,fill=(shade,shade,shade))

    for value,cols in ((name,g["name_columns"]),(school,g["school_columns"])):
        for char,col in zip(value,cols):
            mark(col[LETTERS.index(char.replace(" ","_"))],3.,offset=offset_mm)
    if grade is not None:mark(g["grade_centers"][grade-1],3.8,offset=offset_mm)
    for q,answer in enumerate(answers or ["-"]*15):
        for letter in answer:
            if letter in "ABCDE":mark(g["answer_rows"][q]["ABCDE".index(letter)],4.5,offset=offset_mm)
    for point,diameter,gray in extra_marks or []:mark(point,diameter,gray)
    pix=page.get_pixmap(dpi=dpi,alpha=False)
    rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    return cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR)


def desk(image,rotation=0,perspective=False,shadow=False):
    image=np.ascontiguousarray(np.rot90(image,rotation))
    h,w=image.shape[:2]
    if perspective:
        src=np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],np.float32)
        dst=np.array([[80,130],[w+35,45],[w+100,h+65],[35,h+130]],np.float32)
        matrix=cv2.getPerspectiveTransform(src,dst)
        image=cv2.warpPerspective(image,matrix,(w+180,h+200),borderValue=(205,205,205))
    else:image=cv2.copyMakeBorder(image,70,70,70,70,cv2.BORDER_CONSTANT,value=(205,205,205))
    if shadow:
        height,width=image.shape[:2]
        ramp=np.linspace(.66,1.,width,dtype=np.float32)[None,:,None]
        image=np.clip(image.astype(np.float32)*ramp,0,255).astype(np.uint8)
    return image


class EngineTests(unittest.TestCase):
    def test_pdf_profile_and_geometry(self):
        g=sheet_geometry()
        self.assertEqual(len(g["name_columns"]),37)
        self.assertEqual(len(g["school_columns"]),12)
        self.assertEqual(len(g["grade_centers"]),6)
        self.assertEqual(len(g["answer_rows"]),15)
        self.assertEqual(len(LETTERS),28)
        data,name=build_sheet_pdf("Comunicacion")
        self.assertIn("comunicacion",name)
        doc=pymupdf.open(stream=data,filetype="pdf")
        p=doc[0]
        self.assertAlmostEqual(p.rect.width/mm,297,places=4)
        self.assertAlmostEqual(p.rect.height/mm,210,places=4)
        circles=[d for d in p.get_drawings() if len(d["items"])==4 and all(i[0]=="c" for i in d["items"])]
        self.assertEqual(len(circles),37*28+12*28+6+15*5+1)
        for block in p.get_text("dict")["blocks"]:
            for line in block.get("lines",[]):
                for span in line["spans"]:
                    self.assertTrue(p.rect.contains(pymupdf.Rect(span["bbox"])),span["text"])

    def test_read_characters_grade_answers_rotation_perspective_shadow(self):
        answers=list("ABCDEABCDEABCDE")
        source=render_sheet(answers=answers)
        for rotation in range(4):
            with self.subTest(rotation=rotation):
                result=read_sheet(desk(source,rotation,perspective=True,shadow=True))
                self.assertEqual(result["name"],"MUÑOZ ANA")
                self.assertEqual(result["school"],"SAN JOSE")
                self.assertEqual(result["grade"],3)
                self.assertEqual(result["answers"],answers)
                self.assertEqual(result["issues"],[])
                self.assertEqual(result["warped_image"].shape,(CANVAS_H,CANVAS_W,3))

    def test_six_grades(self):
        for grade in range(1,7):
            with self.subTest(grade=grade):
                result=read_sheet(desk(render_sheet(grade=grade)))
                self.assertEqual(result["grade"],grade)
                self.assertEqual(result["answers"],["-"]*15)
                self.assertFalse(result["needs_review"])

    def test_all_letters_and_last_character_positions(self):
        name="ABCDEFGHIJKLMNÑOPQRSTUVWXYZ ABCDEFGHI"
        school="ZÑ ABCDEFGHI"
        self.assertEqual(len(name),37)
        self.assertEqual(len(school),12)
        result=read_sheet(desk(render_sheet(name=name,school=school,grade=6),perspective=True))
        self.assertEqual(result["name"],name)
        self.assertEqual(result["school"],school)
        self.assertFalse(result["needs_review"])

    def test_missing_internal_character_and_double_grade_require_review(self):
        g=sheet_geometry()
        extra=[(g["name_columns"][1][LETTERS.index("N")],3.,1.),
               (g["grade_centers"][0],3.8,.12)]
        result=read_sheet(desk(render_sheet(name="ANA",extra_marks=extra)))
        self.assertEqual(result["name"],"A A")
        self.assertIsNone(result["grade"])
        self.assertTrue(result["needs_review"])
        self.assertTrue(any("Grado" in issue for issue in result["issues"]))

    def test_shifted_marks_and_double_answer(self):
        answers=["A","BC"]+["-"]*13
        result=read_sheet(desk(render_sheet(answers=answers,offset_mm=(.23,.20))))
        self.assertEqual(result["name"],"MUÑOZ ANA")
        self.assertEqual(result["answers"],["A","X"]+["-"]*13)
        grade=grade_answers(result["answers"],["A"]*15)
        self.assertEqual((grade["correct"],grade["incorrect"],grade["blank"],grade["double"]),(1,1,13,1))
        self.assertEqual(grade["score"],9)
        self.assertEqual(annotate_reading(result,grade).shape,result["warped_image"].shape)

    def test_uncertain_faint_and_erased_marks_require_review(self):
        g=sheet_geometry()
        extra=[(g["answer_rows"][0][0],4.5,.79),
               (g["name_columns"][0][1],3.,.79)]
        result=read_sheet(desk(render_sheet(extra_marks=extra)))
        self.assertEqual(result["answers"][0],"?")
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["name"],"MUÑOZ ANA")
        self.assertTrue(any("Nombre" in note for note in result["decisions"]))
        with self.assertRaises(ValueError):grade_answers(result["answers"],["A"]*15)

    def test_compressed_camera_photo_and_low_resolution_rejection(self):
        source=render_sheet(dpi=160,answers=list("ABCDEABCDEABCDE"))
        photo=desk(source,rotation=1,perspective=True,shadow=True)
        photo=cv2.GaussianBlur(photo,(3,3),.55)
        noise=np.random.default_rng(25).normal(0,2,photo.shape)
        photo=np.clip(photo.astype(np.float32)+noise,0,255).astype(np.uint8)
        ok,encoded=cv2.imencode(".jpg",photo,[cv2.IMWRITE_JPEG_QUALITY,60])
        self.assertTrue(ok)
        result=read_sheet(cv2.imdecode(encoded,cv2.IMREAD_COLOR))
        self.assertEqual(result["name"],"MUÑOZ ANA")
        self.assertEqual(result["school"],"SAN JOSE")
        self.assertEqual(result["answers"],list("ABCDEABCDEABCDE"))
        self.assertFalse(result["needs_review"])
        tiny=render_sheet(dpi=70)
        with self.assertRaises(ValueError):read_sheet(desk(tiny))

    def test_lateral_tracking_guide_mismatch_requires_review(self):
        photo=render_sheet(dpi=220)
        scale=220/25.4
        x,y=7*scale,105*scale
        cv2.rectangle(photo,(int(x-2*scale),int(y-2*scale)),
                      (int(x+2.5*scale),int(y+2*scale)),(255,255,255),-1)
        x+=.8*scale
        cv2.rectangle(photo,(int(x-1.5*scale),int(y-1.5*scale)),
                      (int(x+1.5*scale),int(y+1.5*scale)),(17,17,17),-1)
        result=read_sheet(desk(photo))
        self.assertEqual(result["name"],"MUÑOZ ANA")
        self.assertTrue(result["needs_review"])
        self.assertTrue(any("guía" in issue.lower() for issue in result["issues"]))

    def test_score_validation_and_english_spanish_weights(self):
        a=["A","B","X"]+["-"]*12
        key=["A"]*15
        spanish=grade_answers(a,key,{"buenas":2,"malas":-.5,"blanco":0})
        english=grade_answers(a,key,{"correct":2,"incorrect":-.5,"blank":0})
        self.assertEqual(spanish,english)
        self.assertEqual(spanish["score"],1.)
        with self.assertRaises(ValueError):grade_answers(a,key,{"buenas":float("nan")})

    def test_blank_name_school_grade_never_accepted(self):
        result=read_sheet(desk(render_sheet(name="",school="",grade=None)))
        self.assertTrue(result["needs_review"])
        self.assertIsNone(result["grade"])
        self.assertEqual(result["name"],"")
        self.assertGreaterEqual(len(result["issues"]),3)

    def test_unrelated_and_old_a5_rejected(self):
        with self.assertRaises(ValueError):read_sheet(np.full((1700,2400,3),255,np.uint8))
        old=Path(__file__).resolve().parents[2]/"omr-ocr-scan-v2"/"samples"/"ficha_comunicacion_a5.pdf"
        if old.is_file():
            doc=pymupdf.open(old)
            pix=doc[0].get_pixmap(dpi=180,alpha=False)
            img=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
            with self.assertRaises(ValueError):read_sheet(cv2.cvtColor(img,cv2.COLOR_RGB2BGR))


if __name__=="__main__":unittest.main()
