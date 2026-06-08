# Test fixtures

## caveat.ttf

[Caveat](https://fonts.google.com/specimen/Caveat) by Pablo Impallari,
distributed under the SIL Open Font License, Version 1.1. The full
license text is in `LICENSE-Caveat.txt` in this directory.

This font is checked into the repository because the smoke tests
depend on a known TTF being available at a known path. The OFL
permits redistribution as long as the license travels with the
font, which `LICENSE-Caveat.txt` accomplishes.

## Adding more fixtures

If you add other test fonts:

1. They must be license-compatible with redistribution
   (OFL, Apache 2.0, MIT are all fine).
2. Include the full license text as `LICENSE-<FontName>.txt`.
3. Update this README with attribution.
4. Add an entry to the smoke tests or a parametrized test if
   you want regression coverage on more than one font.
