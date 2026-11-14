"""Editorial release matrix. Names and dimensions are part of the public API."""
from fractions import Fraction

STYLES = ('smpte', 'simple', 'tail')
RATES = {'2397': Fraction(24000, 1001), '24': Fraction(24), '25': Fraction(25)}
SIZES = {
    'hd': (1920, 1080), 'hd-scope': (1920, 1080),
    'uhd': (3840, 2160), 'uhd-scope': (3840, 2160),
    '2k-full': (2048, 1080), '4k-full': (4096, 2160),
    '2k-flat': (1998, 1080), '4k-flat': (3996, 2160),
    '2k-scope': (2048, 858), '4k-scope': (4096, 1716),
}
ACTIVE_ASPECT = {'hd-scope': 2.39, 'uhd-scope': 2.39}


def plan(style, nominal):
    """(total frames, zero-based pop frame) for `nominal` frames per second.

    Head leaders (smpte, simple): pop 2 s before first frame of action, which
    follows the leader's last frame. Tail leader: placed directly after the
    last frame of action; pop 2 s after it.
    """
    return 8 * nominal, (2 if style == 'tail' else 6) * nominal


def active_rect(W, H, active_aspect=None):
    """Centered active area. Mattes are symmetric and each a multiple of
    2 pixels, so offsets stay even (1920x804 / 3840x1608 for 2.39)."""
    if active_aspect is None or abs(active_aspect - W / H) < 1e-3:
        return (0, 0, W, H)
    if active_aspect > W / H:
        matte = 4 * round((H - W / active_aspect) / 4)
        return (0, matte // 2, W, H - matte // 2)
    matte = 4 * round((W - H * active_aspect) / 4)
    return (matte // 2, 0, W - matte // 2, H)


def matrix():
    return {f'{style}/{style}_{variant}_{label}.mov': (style, label, size)
            for style in STYLES for variant, size in SIZES.items() for label in RATES}
