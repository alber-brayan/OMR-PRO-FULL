"""Reglas de espacios, final de texto y selección del relleno dominante."""
import unittest
from unittest.mock import patch

from omr25 import _selection, _read_text, read_sheet, sheet_geometry, LETTERS
from tests.test_omr25_engine import render_sheet, desk


def scores(letter=None, value=.8):
    result=[.0]*28
    if letter:result[LETTERS.index(letter)]=value
    return result


class ReadingRuleTests(unittest.TestCase):
    def test_one_or_two_empty_columns_are_spaces_three_finish(self):
        sequence=[scores('A'),scores(),scores(),scores('B'),scores(),scores(),scores(),scores('Z')]
        issues=[]
        with patch('omr25._measure',side_effect=sequence) as measure:
            text,states,_,selected=_read_text(None,sheet_geometry()['name_columns'],'Nombre',issues)
        self.assertEqual(text,'A  B')
        self.assertEqual(measure.call_count,7)
        self.assertEqual(states[7:],["after_end"]*30)
        self.assertEqual(len(selected),37)
        self.assertFalse(issues)

    def test_explicit_space_marks_do_not_end_text(self):
        sequence=[scores('A'),scores('_'),scores('_'),scores('_'),scores('B')]+[scores()]*3
        with patch('omr25._measure',side_effect=sequence):
            text,*_=_read_text(None,sheet_geometry()['name_columns'],'Nombre',[])
        self.assertEqual(text,'A   B')

    def test_erased_residue_does_not_win_over_full_mark(self):
        for values,expected in (([.80,.42,.01],0),([.45,.85,.02],1),([.18,.70,.17],1)):
            with self.subTest(values=values):self.assertEqual(_selection(values),('single',[expected]))

    def test_near_ties_and_only_faint_marks_stay_ambiguous(self):
        self.assertEqual(_selection([.6909,.6918,.01])[0],'double')
        self.assertEqual(_selection([.25,.17,.01])[0],'uncertain')
        self.assertEqual(_selection([.01,.01,.0])[0],'blank')

    def test_real_pdf_blank_spaces_erased_marks_and_end_of_text(self):
        g=sheet_geometry()
        extra=[(g['name_columns'][1][LETTERS.index('N')],3.,1.),
               (g['name_columns'][8][LETTERS.index('Z')],3.,.12),
               (g['school_columns'][1][LETTERS.index('N')],3.,1.),
               (g['name_columns'][0][LETTERS.index('B')],3.,.70),
               (g['answer_rows'][0][1],4.5,.66),
               (g['answer_rows'][1][3],4.5,.45)]
        result=read_sheet(desk(render_sheet(name='ANA',school='ANA',answers=['A','C','AB']+['-']*12,extra_marks=extra)))
        self.assertEqual(result['name'],'A A')
        self.assertEqual(result['school'],'A A')
        self.assertEqual(result['answers'][:3],['A','C','X'])
        self.assertEqual(result['name_states'][8],'after_end')
        self.assertTrue(result['decisions'])
        self.assertTrue(any('Pregunta 3' in issue for issue in result['issues']))


if __name__=='__main__':unittest.main()
