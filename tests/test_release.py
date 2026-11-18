import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import generate as g
from media_spec import active_rect, matrix
from package_release import package
from verify_media import inspect_media, mov_colr, verify_archive, verify_tree


class ReleaseTests(unittest.TestCase):
    def test_invalid_options_leave_no_output(self):
        for flag, value in [('--fps', '23.5'), ('--fps', '0'), ('--fps', 'nan'),
                            ('--width', '321'), ('--height', '-2'),
                            ('--active-aspect', '0'), ('--active-aspect', 'inf'),
                            ('--pop-dbfs', '6'), ('--pop-dbfs', 'nan')]:
            with self.subTest(flag=flag, value=value), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / 'invalid.mov'
                r = subprocess.run(['.venv/bin/python', 'generate.py', '--style', 'simple',
                                    '--fps', '24', '--width', '320', '--height', '180',
                                    '--out', str(out), flag, value], capture_output=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_exact_audio_lengths(self):
        for fps, samples in [(23.976, 384384), (24, 384000), (25, 384000)]:
            total, pop = g.plan_simple(fps)
            with wave.open(io.BytesIO(g.make_audio_wav_bytes(total, fps, pop))) as w:
                self.assertEqual(w.getnframes(), samples)
                self.assertEqual(w.getparams()[:3], (2, 3, 48000))

    def test_failed_encoder_preserves_previous_movie(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'existing.mov'
            out.write_bytes(b'previous good version')
            with patch.object(g, 'render_frame', side_effect=RuntimeError('render interrupted')):
                with self.assertRaises(RuntimeError):
                    g.encode('simple', 24, 320, 180, None, out)
            self.assertEqual(out.read_bytes(), b'previous good version')
            self.assertEqual(list(Path(tmp).iterdir()), [out])

    def test_encoder_failure_before_start_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'output.mov'
            with patch.object(g.subprocess, 'Popen', side_effect=FileNotFoundError('ffmpeg')):
                with self.assertRaises(FileNotFoundError):
                    g.encode('simple', 24, 320, 180, None, out)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_fresh_render_and_validator_reject_shifted_pop(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'simple_hd_2397.mov'
            g.encode('simple', 23.976, 320, 180, None, out)
            good = inspect_media(out, style='simple', fps_label_value='2397', size=(320, 180))
            self.assertEqual(good['errors'], [])
            self.assertEqual(good['audio_samples'], 384384)
            shifted = Path(tmp) / 'shifted.mov'
            subprocess.run(['ffmpeg', '-v', 'error', '-i', str(out), '-c:v', 'copy',
                            '-af', 'adelay=10:all=1,atrim=duration=8.008', '-c:a', 'pcm_s24le',
                            str(shifted)], check=True)
            bad = inspect_media(shifted, style='simple', fps_label_value='2397', size=(320, 180))
            self.assertIn('pop containment', bad['errors'])

    def test_incomplete_matrix_cannot_replace_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'out'; root.mkdir()
            out = Path(tmp) / 'release.zip'; out.write_bytes(b'previous release')
            with self.assertRaises(ValueError):
                package(root, Path(tmp) / 'README.txt', out)
            self.assertEqual(out.read_bytes(), b'previous release')
            self.assertFalse(verify_tree(root)['ok'])

    def test_archive_rejects_unexpected_paths(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'bad.zip'
            with zipfile.ZipFile(out, 'w') as z:
                z.writestr('../escape.mov', b'bad')
            self.assertFalse(verify_archive(out)['ok'])
            self.assertFalse((Path(tmp) / 'escape.mov').exists())

    def test_true_flat_and_full_are_distinct(self):
        self.assertEqual(len(matrix()), 90)
        self.assertEqual(matrix()['smpte/smpte_2k-flat_24.mov'][2], (1998, 1080))
        self.assertEqual(matrix()['smpte/smpte_2k-full_24.mov'][2], (2048, 1080))

    def test_letterbox_mattes_are_even_and_symmetric(self):
        self.assertEqual(active_rect(1920, 1080, 2.39), (0, 138, 1920, 942))
        self.assertEqual(active_rect(3840, 2160, 2.39), (0, 276, 3840, 1884))
        for W, H, a in [(1920, 1080, 2.39), (3840, 2160, 2.39), (1920, 1080, 1.33), (2048, 1080, 1.85)]:
            x0, y0, x1, y1 = active_rect(W, H, a)
            self.assertEqual((x0, y0), (W - x1, H - y1))
            self.assertEqual((x0 % 2, y0 % 2), (0, 0))

    def test_tail_pop_two_seconds_after_action_and_colr_tagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'tail_hd_2397.mov'
            g.encode('tail', 23.976, 320, 180, 2.39, out)
            r = inspect_media(out, style='tail', fps_label_value='2397', size=(320, 180))
            self.assertEqual(r['errors'], [])
            self.assertEqual((r['pop_frame'], r['pop_start_sample']), (48, 96096))
            self.assertEqual(mov_colr(out), (1, 1, 1))
            # A container without colr must fail even though ffprobe still
            # reports BT.709 from the ProRes frame header.
            untagged = Path(tmp) / 'untagged.mov'
            data = out.read_bytes()
            self.assertEqual(data.count(b'colr'), 1)
            untagged.write_bytes(data.replace(b'colr', b'skip'))
            bad = inspect_media(untagged, style='tail', fps_label_value='2397', size=(320, 180))
            self.assertTrue(any('colr' in e for e in bad['errors']))

    def test_readme_download_table_is_current(self):
        r = subprocess.run(['.venv/bin/python', 'docs/download_table.py', '--check'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_make_matrix_matches_validator(self):
        targets = subprocess.check_output(['make', '--no-print-directory', '-s', 'list'], text=True).splitlines()
        self.assertEqual(set(targets), {'out/' + name for name in matrix()})

    def test_packaging_checks_staged_and_archived_content(self):
        import zipfile
        # A one-file fixture exercises the real packaging/decode path cheaply.
        spec = {'simple/simple_hd_24.mov': ('simple', '24', (320, 180))}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'out'
            movie = root / 'simple/simple_hd_24.mov'
            g.encode('simple', 24, 320, 180, None, movie)
            readme = Path(tmp) / 'README.txt'; readme.write_text('fixture notes')
            archive = Path(tmp) / 'release.zip'
            with patch('package_release.matrix', return_value=spec), patch('verify_media.matrix', return_value=spec), patch('verify_swatches.matrix', return_value=spec):
                package(root, readme, archive)
                self.assertTrue(verify_archive(archive)['ok'])
                original = archive.read_bytes()
                movie.write_bytes(b'incomplete movie')
                with self.assertRaises(ValueError):
                    package(root, readme, archive)
                self.assertEqual(archive.read_bytes(), original)
                # A readable ZIP can still contain altered delivery notes.
                tampered = Path(tmp) / 'tampered.zip'
                with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, 'w') as dst:
                    for name in src.namelist():
                        dst.writestr(name, b'altered notes' if name == 'README.txt' else src.read(name))
                self.assertFalse(verify_archive(tampered)['ok'])


if __name__ == '__main__':
    unittest.main()
