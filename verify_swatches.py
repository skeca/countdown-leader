#!/usr/bin/env python3
"""Check BT.709 swatch values and report edge extrema."""
import argparse
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from media_spec import ACTIVE_ASPECT, RATES, active_rect, matrix
from verify_media import run


def inspect_swatches(root, name, spec):
    _, label, (w, h) = spec
    errors = []
    result = {'file': name, 'errors': errors}
    try:
        # Exact first frame of the "7" interval, not an arbitrary time seek.
        frame = round(RATES[label])
        data = run(['ffmpeg', '-v', 'error', '-threads', '2', '-i', str(Path(root) / name),
                    '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1', '-an',
                    '-pix_fmt', 'yuv422p10le', '-f', 'rawvideo', '-'])
        raw = np.frombuffer(data, '<u2')
        y = raw[:w*h].reshape(h, w)
        cb = raw[w*h:w*h*3//2].reshape(h, w//2)
        cr = raw[w*h*3//2:].reshape(h, w//2)
        variant = Path(name).stem.split('_')[1]
        _, y0, _, y1 = active_rect(w, h, ACTIVE_ASPECT.get(variant))
        ah = y1 - y0
        chip = int(w*2*.038) / 2
        left = int(w*2*.97)/2 - int(w*2*.038)*8/2
        yy = y0 + int(ah*.89)
        patches, expected = [], []
        for i, rgb in enumerate([(1,1,1), (1,1,0), (0,1,1), (0,1,0),
                                 (1,0,1), (1,0,0), (0,0,1), (0,0,0)]):
            x = int(left + (i+.5)*chip)
            patches.append([int(np.median(z[yy-2:yy+3, xx-2:xx+3]))
                            for z, xx in [(y,x), (cb,x//2), (cr,x//2)]])
            r, g, b = rgb
            lum = .2126*r + .7152*g + .0722*b
            expected.append([round(64 + 876*lum), round(512 + 896*(b-lum)/1.8556),
                             round(512 + 896*(r-lum)/1.5748)])
        delta = int(np.abs(np.array(patches) - expected).max())
        if delta > 3:
            errors.append(f'BT.709 bar interiors differ by {delta} codes (tolerance 3)')
        gray = []
        for i in range(11):
            x = int(w*.03 + (i+.5)*chip)
            gray.append(int(np.median(y[yy-2:yy+3, x-2:x+3])))
        if not (abs(gray[0]-64) <= 3 and abs(gray[-1]-940) <= 3 and all(a < b for a,b in zip(gray,gray[1:]))):
            errors.append('grayscale endpoints or monotonicity')
        result.update(sample_frame=frame, bars_ycbcr=patches, expected_bt709=expected,
                      max_bar_error=delta, grayscale_y=gray,
                      sampled_frame_extrema={k: [int(z.min()), int(z.max())]
                                             for k,z in [('y',y),('cb',cb),('cr',cr)]})
    except Exception as exc:
        errors.append(str(exc))
    return result


def verify_swatches(root, jobs=2):
    items = [(name, spec) for name, spec in matrix().items() if spec[0] == 'smpte']
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(lambda item: inspect_swatches(root, *item), items))
    return {'ok': all(not row['errors'] for row in rows), 'sampled_files': rows,
            'scope': 'One native countdown frame per SMPTE variant; no strict all-frame level-limit claim.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('out'))
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    report = verify_swatches(args.root)
    content = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(content)
    print(content)
    raise SystemExit(0 if report['ok'] else 1)
