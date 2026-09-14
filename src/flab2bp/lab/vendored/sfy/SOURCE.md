# Vendored FactorioLab Satisfactory dataset

These two files are the bytes FactorioLab serves, unmodified -- no reformatting,
no re-indentation, no key reordering. They are what makes `sfy` support work
offline and what keeps the test suite hermetic.

Fetched: 2026-09-14

| File | Source URL | sha256 |
| --- | --- | --- |
| `data.json` | <https://factoriolab.github.io/data/sfy/data.json> | `3d931bb39a9ed08ee01162f530423e1b128d42897e97c733c3a275b52b5e269d` |
| `hash.json` | <https://factoriolab.github.io/data/sfy/hash.json> | `ace8a9afc1bc20a2703c404b6b692563dfd8455263c2ec0a7b46f0626da52dfb` |

`data.json` reports `version: {"Satisfactory": "1.2"}`.

To refresh, re-fetch both URLs byte for byte and update the digests above:

```sh
curl -sSf -o data.json https://factoriolab.github.io/data/sfy/data.json
curl -sSf -o hash.json https://factoriolab.github.io/data/sfy/hash.json
sha256sum data.json hash.json
```
