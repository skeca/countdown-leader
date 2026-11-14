#!/usr/bin/env python3
"""Validate editorial media and exact release membership; fail closed on errors."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import numpy as np
from media_spec import RATES, SIZES, matrix, plan


def run(args):
    p = subprocess.run(args, capture_output=True)
    if p.returncode or p.stderr:
        raise RuntimeError(p.stderr.decode(errors='replace').strip() or f'exit {p.returncode}')
    return p.stdout


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def mov_colr(path):
    """(primaries, transfer, matrix) from the video sample entry's colr atom.

    ffprobe reports the ProRes frame header, which hides a missing container
    tag; AVFoundation/QuickTime read this atom.
    """
    import struct
    with open(path, 'rb') as f:
        data = f.read()
    def boxes(start, end):
        while start + 8 <= end:
            size, kind = struct.unpack('>I4s', data[start:start + 8]); head = 8
            if size == 1:
                size, head = struct.unpack('>Q', data[start + 8:start + 16])[0], 16
            elif size == 0:
                size = end - start
            if size < head:
                return
            yield kind, start + head, start + size
            start += size
    def find(start, end, path):
        for kind, body, stop in boxes(start, end):
            if kind == path[0]:
                if len(path) == 1:
                    yield body, stop
                else:
                    yield from find(body, stop, path[1:])
    for body, stop in find(0, len(data), [b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd']):
        entry = body + 8                       # version/flags + entry count
        size, fmt = struct.unpack('>I4s', data[entry:entry + 8])
        if fmt != b'apch':
            continue
        for kind, cbody, _ in boxes(entry + 86, entry + size):   # after VisualSampleEntry
            if kind == b'colr' and data[cbody:cbody + 4] in (b'nclc', b'nclx'):
                return struct.unpack('>HHH', data[cbody + 4:cbody + 10])
    return None


def inspect_media(path, *, style=None, fps_label_value=None, size=None, pop_dbfs=-20):
    """Fully decode video/audio; check frame boundaries independently of renderer."""
    path = Path(path)
    errors = []
    result = {'file': str(path), 'errors': errors}

    def check(ok, name):
        if not ok:
            errors.append(name)

    try:
        if style is None:
            style, variant, fps_label_value = path.stem.split('_')
            size = SIZES[variant]
        rate = RATES[fps_label_value]
        nominal = round(rate)
        total, pop = plan(style, nominal)
        info = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
        streams = info['streams']
        check(len(streams) == 2, 'expected exactly video and audio streams')
        v = next(s for s in streams if s['codec_type'] == 'video')
        a = next(s for s in streams if s['codec_type'] == 'audio')
        for k, expected in {'codec_name': 'prores', 'profile': 'HQ', 'codec_tag_string': 'apch',
                            'pix_fmt': 'yuv422p10le', 'color_space': 'bt709',
                            'color_transfer': 'bt709', 'color_primaries': 'bt709',
                            'field_order': 'progressive', 'nb_frames': str(total),
                            'r_frame_rate': f'{rate.numerator}/{rate.denominator}',
                            'avg_frame_rate': f'{rate.numerator}/{rate.denominator}',
                            'sample_aspect_ratio': '1:1'}.items():
            check(v.get(k) == expected, f'video {k}: {v.get(k)} != {expected}')
        check((v['width'], v['height']) == size, 'dimensions')
        # ProRes is always video range and signals no range flag; FFmpeg >= 7
        # reports 'tv', older versions leave it unset. Levels are measured separately.
        check(v.get('color_range') in ('tv', None, 'unknown'), f"video color_range: {v.get('color_range')}")
        check(mov_colr(path) == (1, 1, 1), f'MOV colr atom: {mov_colr(path)} != (1, 1, 1)')
        for k, expected in {'codec_name': 'pcm_s24le', 'sample_rate': '48000',
                            'channels': 2, 'bits_per_sample': 24, 'channel_layout': 'stereo'}.items():
            check(a.get(k) == expected, f'audio {k}')
        check(float(v['start_time']) == float(a['start_time']) == 0, 'stream starts')
        duration = total / rate
        check(abs(float(v['duration']) - float(duration)) < 1e-6, 'video duration')
        check(abs(float(a['duration']) - float(duration)) <= 1 / 48000, 'audio duration')
        # Check presentation timestamps: count alone would miss gaps or repeats.
        timestamps = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                     '-show_packets', '-show_entries', 'packet=pts_time,duration_time',
                                     '-of', 'json', str(path)]))['packets']
        check(len(timestamps) == total, 'packet count')
        check(all(abs(float(p['pts_time']) - float(i / rate)) <= 1e-6 and
                  abs(float(p['duration_time']) - float(1 / rate)) <= 1e-6
                  for i, p in enumerate(timestamps)), 'video timestamps/cadence')
        audio = np.frombuffer(run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path),
                                  '-map', '0:a:0', '-f', 's32le', '-']), '<i4').reshape(-1, 2)
        check(np.array_equal(audio[:, 0], audio[:, 1]), 'dual mono')
        start, end = round(pop / rate * 48000), round((pop + 1) / rate * 48000)
        nz = np.flatnonzero(audio[:, 0])
        check(len(audio) == round(duration * 48000), 'sample count')
        check(len(nz) > 0 and nz[0] >= start and nz[-1] < end, 'pop containment')
        # Fades can silence endpoint samples, but cannot move/shorten the burst.
        check(len(nz) > 0 and nz[0] <= start + 2 and nz[-1] >= end - 3, 'pop boundaries')
        check(np.count_nonzero(audio[start:end, 0]) > .90 * (end - start), 'pop occupancy')
        peak = float(20 * np.log10(max(float(np.abs(audio.astype(float)).max()), 1) / 2**31))
        if pop_dbfs is not None:
            check(abs(peak - pop_dbfs) < .02, 'pop level')
        tone = audio[start + 120:end - 120, 0].astype(float)
        crossings = np.flatnonzero((tone[:-1] <= 0) & (tone[1:] > 0))
        hz = 48000 / float(np.mean(np.diff(crossings))) if len(crossings) > 1 else 0
        check(abs(hz - 1000) < 2, 'pop frequency')
        frames = np.frombuffer(run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '2',
                                    '-i', str(path), '-map', '0:v:0', '-an',
                                    '-vf', 'scale=64:36', '-filter_threads', '1',
                                    '-pix_fmt', 'gray', '-f', 'rawvideo', '-']), np.uint8).reshape(-1, 36, 64)
        check(len(frames) == total, 'decoded frame count')
        bright = np.flatnonzero(frames.max(axis=(1, 2)) > 10).tolist()
        check(bright == (list(range(pop + 1)) if style == 'smpte' else [pop]), 'picture/black schedule')
        result.update(sha256=sha256(path), decoded_frames=len(frames), audio_samples=len(audio),
                      pop_frame=pop, pop_start_sample=start, peak_dbfs=peak, frequency_hz=hz,
                      duration_seconds=float(duration), pop_offset_seconds=float((total - pop if style != 'tail' else pop) / rate))
    except Exception as exc:
        errors.append(str(exc))
    return result


def verify_tree(root, jobs=2):
    root = Path(root)
    expected = matrix()
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*.mov')}
    errors = []
    if actual != set(expected):
        errors.append({'missing': sorted(set(expected) - actual), 'unexpected': sorted(actual - set(expected))})
    def inspect(name):
        style, label, size = expected[name]
        row = inspect_media(root / name, style=style, fps_label_value=label, size=size)
        row['file'] = name
        return row
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(inspect, sorted(set(expected) & actual)))
    return {'ok': not errors and all(not row['errors'] for row in rows),
            'membership_errors': errors, 'files': rows}


def verify_archive(path, jobs=2):
    try:
        with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory(prefix='leader-verify-') as tmp:
            expected = {'README.txt', 'manifest.json'} | {'leaders/' + name for name in matrix()}
            names = z.namelist()
            if len(names) != len(set(names)) or set(names) != expected:
                raise ValueError('archive must contain exactly README.txt, manifest.json and the release matrix')
            # Exact allowlist above excludes traversal paths and symlinks are not followed.
            z.extractall(tmp)
            manifest = json.loads((Path(tmp) / 'manifest.json').read_text())
            report = verify_tree(Path(tmp) / 'leaders', jobs)
            checksums = {row['file']: row.get('sha256') for row in report['files']}
            report['manifest_matches'] = (manifest.get('sha256') == checksums and
                manifest.get('readme_sha256') == sha256(Path(tmp) / 'README.txt'))
            report['ok'] = report['ok'] and report['manifest_matches']
            return report
    except Exception as exc:
        return {'ok': False, 'errors': [str(exc)]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('files', nargs='*', type=Path, help='individual named matrix files')
    p.add_argument('--root', type=Path, default=Path('out'))
    p.add_argument('--archive', type=Path)
    p.add_argument('--jobs', type=int, default=2, choices=range(1, 5))
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    if args.archive:
        report = verify_archive(args.archive, args.jobs)
    elif args.files:
        rows = [inspect_media(f) for f in args.files]
        report = {'ok': all(not r['errors'] for r in rows), 'files': rows}
    else:
        report = verify_tree(args.root, args.jobs)
    content = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(content)
    print(f"{'PASS' if report['ok'] else 'FAIL'}: {len(report.get('files', []))} movies checked")
    if not report['ok']:
        print(content, file=sys.stderr)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
