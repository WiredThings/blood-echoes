"""Authored ar containers, not executable artifacts or game data."""
import io,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from archive_identity import digests
def member(name,body,date='123',uid='501',gid='20',mode='100644'):
 fields=[name.ljust(16),date.ljust(12),uid.ljust(6),gid.ljust(6),mode.ljust(8),str(len(body)).ljust(10),'`\n']
 header=''.join(fields).encode();assert len(header)==60
 return header+body+(b'\n'if len(body)%2 else b'')
def identity(raw):return digests(io.BytesIO(raw),1<<20)
class ArchiveIdentity(unittest.TestCase):
 def test_container_metadata_only(self):
  a=b'!<arch>\n'+member('object.o/',b'authored code and data')
  b=b'!<arch>\n'+member('object.o/',b'authored code and data','456','0','0')
  x,y=identity(a),identity(b);self.assertNotEqual(x[0],y[0]);self.assertEqual(x[1],y[1]);self.assertEqual(x[2],len(a))
 def test_every_member_and_symbol_index_retained(self):
  sym=member('#1/12',b'__.SYMDEF\0\0\0'+b'authored symbol offsets')
  base=b'!<arch>\n'+sym+member('object.o/',b'authored code')
  for changed in (b'!<arch>\n'+sym+member('object.o/',b'changed code'),b'!<arch>\n'+member('#1/12',b'__.SYMDEF\0\0\0'+b'changed symbol offsets')+member('object.o/',b'authored code'),b'!<arch>\n'+sym+member('renamed.o/',b'authored code'),b'!<arch>\n'+sym+member('object.o/',b'authored code',mode='100755'),b'!<arch>\n'+member('object.o/',b'authored code')+sym):
   self.assertNotEqual(identity(base)[1],identity(changed)[1])
 def test_nonarchive_byte_identity(self):
  a=identity(b'authored non-archive binary input');self.assertEqual(a[0],a[1])
 def test_bad_or_external_containers_refused(self):
  valid=b'!<arch>\n'+member('object.o/',b'abc')
  bad=[b'!<thin>\n',b'!<arch>\ntruncated',valid[:-2],valid[:-1]+b'x',valid[:56]+b'9999999999'+valid[66:]]
  for raw in bad:
   with self.assertRaises(ValueError):identity(raw)
 def test_budget_refused(self):
  with self.assertRaises(ValueError):digests(io.BytesIO(b'!<arch>\n'+member('obj.o/',b'x'*100)),70)
if __name__=='__main__':unittest.main()
