"""Fail-closed controls for the private image action API; no game started."""
from pathlib import Path
import json,sys,tempfile,unittest
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from qa_image_action import tokens,regular
class Controls(unittest.TestCase):
 def test_original_pad_tokens(self):
  self.assertEqual(tokens(' lx=128  ly=0 r1 '),'lx=128 ly=0 r1')
  self.assertEqual(tokens(''),'')
 def test_no_restart_or_menu_or_unbounded_axes(self):
  for text in ['quickload','quicksave','options','cross','replay','F3','lx=256','lx=-1','lx=0 lx=128','r1 r1','ry=0;exit','x'*129]:
   with self.subTest(text=text),self.assertRaises(ValueError):tokens(text)
 def test_owned_file_and_size(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'file';p.write_bytes(b'ok')
   self.assertEqual(regular(p,2),b'ok')
   with self.assertRaises(ValueError):regular(p,1)
   alias=p.with_name('alias');alias.symlink_to(p)
   with self.assertRaises(ValueError):regular(alias,2)
 def test_hardlinked_file_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'file';p.write_bytes(b'ok');q=p.with_name('hardlink');q.hardlink_to(p)
   with self.assertRaises(ValueError):regular(p,2)
if __name__=='__main__':unittest.main()
