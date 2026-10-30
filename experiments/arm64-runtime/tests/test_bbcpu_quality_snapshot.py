"""QA keeps original rendering choices and cannot import formal progress paths."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from package_bbcpu_source_qa import apply_quality
class QualityTests(unittest.TestCase):
    def test_only_rendering_choices_transferred(self):
        qa=dict(userFolder='/isolated/user',gameFolder='/isolated/game',extraEnv='BB_AUDIO=none',online=False,cloudSaves=False,cpuBackend='bbcpu')
        original=dict(renderer='metal',resolution='2560x1440',metalOcclusion=True,metalOptimizations={'BB_MTL_DEPTH_LAYOUT':True},userFolder='/formal/user',gameFolder='/formal/game',online=True,extraEnv='secret',credential='never',cpuBackend='fex')
        selected=apply_quality(qa,original)
        self.assertEqual(qa['resolution'],'2560x1440');self.assertTrue(qa['metalOcclusion']);self.assertEqual(qa['userFolder'],'/isolated/user');self.assertFalse(qa['online']);self.assertEqual(qa['cpuBackend'],'bbcpu');self.assertNotIn('credential',qa);self.assertNotIn('extraEnv',selected)
    def test_unsupported_renderer_is_not_silently_changed(self):
        with self.assertRaisesRegex(ValueError,'renderer'):apply_quality({},dict(renderer='vulkan'))
if __name__=='__main__':unittest.main()
