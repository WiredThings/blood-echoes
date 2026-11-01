import tempfile
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qa_exploration_route import pad_samples, input_phases, interval, camera_path, match_paths


class RouteChecks(unittest.TestCase):
    def test_existing_recording_phases_and_release(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'route.pad'
            p.write_text('0 0 128 128 128 128 0 0\n1000 0 128 128 200 128 0 0\n3000 0 128 0 128 128 0 0\n6000 0 128 128 128 128 0 0\n')
            self.assertEqual([x['kind'] for x in input_phases(pad_samples(p))], ['neutral', 'turning', 'walking'])
            p.write_text(p.read_text().replace('6000 0 128 128', '6000 0 128 0'))
            with self.assertRaisesRegex(ValueError, 'released'):
                pad_samples(p)

    def test_long_frame_is_not_removed_for_missing_camera(self):
        rows = [dict(t_s='.02', frame_ms='20', cam_x='1', cam_y='2', cam_z='3'),
                dict(t_s='.52', frame_ms='500', cam_x='', cam_y='', cam_z='')]
        result = interval(rows, 0, .52)
        self.assertEqual(result['frames'], 2)
        self.assertEqual(result['long_frames']['200'], 1)
        self.assertEqual(result['max_frame_ms'], 500)

    def test_backwards_frame_clock_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'backwards'):
            interval([dict(t_s='1', frame_ms='20'), dict(t_s='.5', frame_ms='20')], 0, 2)

    def test_same_input_with_fall_is_not_matching_workload(self):
        a = [(0, (0, 0, 0)), (1, (1, 0, 0))]
        self.assertTrue(match_paths(a, a, 1)['qualified'])
        self.assertFalse(match_paths(a, [(0, (0, 0, 0)), (1, (1, -3, 0))], 1)['qualified'])
        self.assertFalse(match_paths(a, [(0, (0, 0, 0)), (1, (5, 0, 0))], 1)['qualified'])

    def test_long_camera_gap_cannot_be_interpolated_into_equivalence(self):
        rows = [dict(t_s=str(t), cam_x='0', cam_y='0', cam_z='1') for t in (0, .2, 1, 1.2)]
        with self.assertRaisesRegex(ValueError, 'gap'):
            camera_path(rows, 0, 1.2)


if __name__ == '__main__':
    unittest.main()
