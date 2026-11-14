#!/usr/bin/env python3
"""Snapshot, validate, archive, revalidate, then atomically publish a release."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from media_spec import matrix
from verify_media import sha256, verify_archive, verify_tree
from verify_swatches import verify_swatches


def package(root, readme, archive, jobs=2):
    root, readme, archive = Path(root), Path(readme), Path(archive)
    # Catch missing/extra movies before copying. In-progress renders never have
    # final .mov names; media validation also catches older incomplete outputs.
    expected = set(matrix())
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*.mov')}
    if actual != expected:
        raise ValueError(f'release matrix mismatch; missing={sorted(expected-actual)}, unexpected={sorted(actual-expected)}')
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.package-', dir=archive.parent) as tmp:
        stage = Path(tmp)
        leaders = stage / 'leaders'
        for name in sorted(expected):
            destination = leaders / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, destination)
        shutil.copyfile(readme, stage / 'README.txt')
        # Validate the snapshot that will actually be shipped, not live files
        # that could be replaced while the archive is being constructed.
        print('Validating staged release...', flush=True)
        report = verify_tree(leaders, jobs)
        if not report['ok']:
            raise ValueError('staged release failed validation: ' + json.dumps(report))
        colors = verify_swatches(leaders, jobs)
        if not colors['ok']:
            raise ValueError('reference swatch validation failed: ' + json.dumps(colors))
        manifest = {'profile': 'custom-editorial-v2',
                    'sha256': {r['file']: r['sha256'] for r in report['files']},
                    'readme_sha256': sha256(stage / 'README.txt')}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staged_zip = stage / 'release.zip'
        with zipfile.ZipFile(staged_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for name in ['README.txt', 'manifest.json'] + ['leaders/' + n for n in sorted(expected)]:
                z.write(stage / name, name)
        print('Validating archived movies and checksums...', flush=True)
        archived = verify_archive(staged_zip, jobs)
        if not archived['ok']:
            raise ValueError('archive failed validation: ' + json.dumps(archived))
        # Publish only after a complete decode and manifest check of the ZIP.
        report_path = archive.with_suffix('.qa.json')
        # Checksums for the zip and for each movie as a standalone release asset.
        sums_path = archive.with_suffix('.sha256')
        (stage / 'sums').write_text(''.join(
            f'{digest}  {name}\n' for name, digest in
            [(archive.name, sha256(staged_zip))] +
            sorted((Path(n).name, d) for n, d in manifest['sha256'].items())))
        (stage / 'qa.json').write_text(json.dumps({'ok': True, 'loose_snapshot': report,
                                                  'archive': archived, 'reference_swatches': colors}, indent=2) + '\n')
        staged_zip.replace(archive)
        (stage / 'qa.json').replace(report_path)
        (stage / 'sums').replace(sums_path)
        print(f'Wrote {archive}: {len(expected)} validated movies; QA: {report_path}', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('version', nargs='?', default=date.today().strftime('%Y%m%d'))
    p.add_argument('--root', type=Path, default=Path('out'))
    p.add_argument('--readme', type=Path, default=Path('bundle/README.txt'))
    p.add_argument('--jobs', type=int, default=2, choices=range(1, 5))
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.version):
        p.error('version must be a filename-safe tag')
    try:
        package(args.root, args.readme, Path('dist') / f'countdown-leader-{args.version}.zip', args.jobs)
    except (ValueError, OSError) as exc:
        p.exit(1, f'Packaging refused: {exc}\n')


if __name__ == '__main__':
    main()
