COUNTDOWN LEADER

Head and tail leaders with a frame-accurate 2-pop. 90 files: three styles,
three frame rates, ten rasters. https://github.com/skeca/countdown-leader

STYLES
  smpte   PICTURE START, 8 to 3 countdown with a sweep, one "2" frame with
          the pop, then black.
  simple  Black, with only the "2" frame and the pop.
  tail    Goes after the last frame of picture. Black, the "2" frame with
          the pop 2 seconds in, then black.

USING THEM
  Put the head leader directly in front of the first frame of picture, with
  no gap and no extra black. The tail goes directly after the last frame.
  Match the timeline rate to the file.

  The pop is 48 frames before picture at 24 and 23.976 fps, and 50 at 25.
  At 23.976 that is 2.002 seconds of real time.

FILES
  leaders/{style}/{style}_{raster}_{rate}.mov, rate tags 2397, 24, 25

  hd          1920 x 1080   full frame
  hd-scope    1920 x 1080   2.39 letterbox, picture 1920 x 804
  uhd         3840 x 2160   full frame
  uhd-scope   3840 x 2160   2.39 letterbox, picture 3840 x 1608
  2k-full     2048 x 1080   full DCI container
  4k-full     4096 x 2160   full DCI container
  2k-flat     1998 x 1080   DCI Flat, 1.85
  4k-flat     3996 x 2160   DCI Flat, 1.85
  2k-scope    2048 x 858    DCI Scope, 2.39
  4k-scope    4096 x 1716   DCI Scope, 2.39

FORMAT
  ProRes 422 HQ, 10-bit 4:2:2, progressive, square pixels. BT.709, limited
  range. No timecode track.
  48 kHz / 24-bit PCM stereo, left and right identical. The pop is one
  frame of 1 kHz at -20 dBFS peak with 2 ms fades. There is no other sound.

CHECKS
  The files are checked for format, picture and sound timing, and pop
  level. manifest.json contains SHA-256 checksums.

LICENSE
  Use, change and redistribute these files however you like, commercially
  too, no credit needed. Generator code: MIT. Fonts: SIL OFL 1.1.
