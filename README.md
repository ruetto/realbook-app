# Real Book Guitar

A Windows app that reads Real Book PDFs and prints each song as a guitar lead sheet: the melody, tab fingered in position, and jazz chord voicings (shells, drop-2, drop-3, rootless) with each note labelled by its chord tone. You can transpose to any key and change tuning.

## Install

Open the [latest release](../../releases/tag/latest), download **RealBookGuitar-Setup.exe** and run it. It installs for your user only and needs no administrator rights.

Windows may warn that the app comes from an unknown publisher, because it isn't code-signed. Click **More info**, then **Run anyway**.

## Use

1. **File → Import a Real Book PDF…**
   - The app finds the songs from the PDF's bookmarks, from an index file (a title and page number per line), or treats each page as one song.
   - Reading takes about 15 seconds a page. An interrupted import continues where it stopped.
2. **Review.** Each song opens on the Review tab, with every line of the scan above what was read.
   - Bars that look wrong are flagged in orange. Double-click a Chords or Melody cell to fix it.
   - Mark the song as checked when it's right.
3. **Lead sheet.** Pick voicings, key, tuning, open strings and hand position, then save the PDF or print it.

Your library lives in `Documents\Real Book Guitar` (you can change this in Settings). The app never modifies your PDFs.

## How it works

- **Page reading.** Staff lines are found on the page directly. Each line of music is read by the JAZZMUS recognizer (University of Alicante and JKU Linz, ISMIR 2025, MIT license; see `models/LICENSE-JAZZMUS`), exported to ONNX. The ONNX version gives token-for-token the same output as the original PyTorch model.
- **Engraving.** Pages are engraved with GNU LilyPond, which is bundled with the app.
- **Checks.** `python -m pytest tests` runs about 930 checks. These cover chord symbols against music21, every voicing in 12 keys × 4 styles × 4 tunings, every tab number, transposition spelling, page reading, and the printed output itself.

## Development

```
pip install -r requirements-dev.txt
python -m pytest tests
python RealBookGuitar.py               # run the app
python RealBookGuitar.py --selftest    # check engraver and recognizer
```

Every push to `main` builds the Windows installer on GitHub Actions and publishes it as the `latest` release.

No Real Book material is stored in this repository; the tests use an original tune written for them.
