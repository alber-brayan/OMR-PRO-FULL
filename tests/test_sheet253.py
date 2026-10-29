"""Evita que las nuevas ayudas impresas se conviertan en falsas marcas."""
import unittest
import cv2
from omr25 import read_sheet, _measure, _selection, sheet_geometry, SX, SY
from omr_regions import register_geometry
from tests.test_omr25_engine import render_sheet, desk


class Sheet253Tests(unittest.TestCase):
    def test_printed_letters_stay_blank_with_shadow_perspective_and_jpeg(self):
        for dpi in (150, 220, 300):
            with self.subTest(dpi=dpi):
                source=desk(render_sheet(name='',school='',grade=None,dpi=dpi),perspective=True,shadow=True)
                _,jpeg=cv2.imencode('.jpg',source,[cv2.IMWRITE_JPEG_QUALITY,78])
                result=read_sheet(cv2.imdecode(jpeg,cv2.IMREAD_COLOR))
                gray=cv2.cvtColor(result['warped_image'],cv2.COLOR_BGR2GRAY)
                g=result['sampling_geometry']
                for col in g['name_columns']+g['school_columns']:
                    self.assertEqual(_selection(_measure(gray,col,3))[0],'blank')
                self.assertEqual(result['answer_states'],['blank']*15)
                self.assertEqual(_selection(result['grade_mark_scores'])[0],'blank')

    def test_sector_guides_are_used_and_missing_guides_fall_back_to_rings(self):
        result=read_sheet(desk(render_sheet(answers=list('ABCDEABCDEABCDE')),perspective=True))
        self.assertEqual(result['answers'],list('ABCDEABCDEABCDE'))
        self.assertTrue(all(q['sector_guides']['used'] for q in result['quality']['region_tracking'].values()))
        gray=cv2.cvtColor(result['warped_image'],cv2.COLOR_BGR2GRAY)
        for points in sheet_geometry()['sector_guides'].values():
            for x,y in points:
                cv2.rectangle(gray,(int((x-1)*SX),int((y-1)*SY)),(int((x+1)*SX),int((y+1)*SY)),255,-1)
        _,quality=register_geometry(gray,sheet_geometry(),SX,SY)
        self.assertTrue(all(not q['sector_guides']['used'] for q in quality.values()))
        self.assertTrue(all(q['reliable'] for q in quality.values()))


if __name__=='__main__':
    unittest.main()
