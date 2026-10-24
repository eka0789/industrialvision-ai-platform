# Dataset Setup Guide

**Status:** verified against provider pages on 2026-10-24. **No dataset is
redistributed in this repository.** Datasets are gitignored; download them
yourself, into your own storage, and record checksums locally.

This guide covers: verified public sources and their license/use restrictions,
download steps, the dataset layout this platform's tooling expects, and how to
record checksums.

## Rules for this repository

1. **Never commit dataset files.** `.gitignore` excludes `datasets/`,
   `data/raw/`, and `data/processed/`. Only reports, configs, and code belong
   in Git.
2. **Never redistribute restricted datasets** (or derived annotation files)
   through this repository, container images, or release artifacts.
3. **Never label synthetic assets as industrial data.** Test fixtures and
   generated images are synthetic; they must be reported as synthetic in any
   model provenance, evaluation, or documentation.
4. **Record provenance per model.** The model registry API requires a license
   and accepts training-data provenance for every registered model; a model
   whose dataset provenance is unknown must say so explicitly.
5. **License obligations are the adopter's responsibility.** The table below
   summarizes what providers state; it is not legal advice. Commercial use of
   non-commercial-licensed data requires separate clearance from the provider.

## Verified public sources

| Dataset | Task fit | License / terms (provider-stated) | Commercial use | Provider checksums | Source |
|---|---|---|---|---|---|
| MVTec AD | Anomaly detection / localization | CC BY-NC-SA 4.0; provider states commercial use not allowed | **Restricted** | Not published on provider page | <https://www.mvtec.com/research-teaching/datasets/mvtec-ad> |
| MVTec LOCO AD | Logical anomaly detection / segmentation | CC BY-NC-SA 4.0; provider states commercial use not allowed | **Restricted** | Not published on provider page | <https://www.mvtec.com/research-teaching/datasets/mvtec-loco-ad> |
| KolektorSDD | Defect detection / segmentation | CC BY-NC-SA 4.0; commercial use by contact with the provider | **Restricted** | Not published | <https://www.vicos.si/resources/kolektorsdd/> |
| DAGM 2007 | Defect detection / weakly supervised localization | CC BY 4.0 (Zenodo record metadata) | Allowed with attribution | **Yes — MD5 per archive on the Zenodo record** | <https://zenodo.org/records/12750201> |
| NEU-DET (NEU surface defect DB) | Object detection (6 hot-rolled steel defect classes) | **No license stated on the provider page** — citation requested | **Uncleared** | Not published | <https://faculty.neu.edu.cn/songkechen/zh_CN/zdylm/263270/list/> |
| GC10-DET | Object detection (10 steel-sheet defect types) | **No license published** (creator GitHub has no license; license question unanswered) | **Uncleared** | Not published | <https://github.com/lvxiaoming2019/GC10-DET-Metallic-Surface-Defect-Datasets> |

Notes on verification:

- MVTec pages were read directly; both datasets are explicitly released under
  CC BY-NC-SA 4.0 with a stated prohibition on commercial use. Download
  requires filling in a request form.
- NEU-DET and GC10-DET have **no explicit license** on their provider pages.
  Silence is not permission: treat both as research-use-uncleared until the
  provider clarifies, and do not ship models trained on them in a commercial
  product without clearance.
- DAGM's currently published record is the Zenodo archive, which states
  CC BY 4.0 and publishes MD5 checksums for each ZIP — the only source in this
  table with provider-published checksums.
- No provider in this table publishes SHA-256 checksums; record your own
  (below) after download.

## Download steps

1. **Pick a dataset whose license fits your use** (see table). If your use is
   commercial, prefer CC BY sources (DAGM) or obtain written clearance.
2. Download **only from the provider page** linked above — never from
   third-party mirrors, and never commit the download.
3. Place the extracted archive under `datasets/<name>/` in your local
   workspace (gitignored), e.g. `datasets/mvtec_ad/`.
4. Verify integrity:
   - DAGM: compare MD5 against the values on the Zenodo record.
   - Others: record your own checksum manifest with the validation tool
     (below) immediately after download, and keep it with your run records.
5. Convert annotations to this platform's layout if the provider uses another
   format (keep the original annotation files untouched alongside; never
   overwrite provider labels).
6. Run the validator (below) and keep the JSON report. **Training runs must
   reference a passing report** — the training entry point refuses datasets
   without one.

## Expected layout

YOLO-style pairing, rooted at `datasets/<name>/`:

```text
datasets/<name>/
├── images/                  # flat layout
│   ├── img_0001.png
│   └── ...
└── labels/                  # one .txt per image, same stem
    ├── img_0001.txt         # "class_id x_center y_center width height" (normalized)
    └── ...
```

Or pre-split layout (also supported; leakage across these directories is
detected):

```text
datasets/<name>/
├── images/{train,val,test}/...
└── labels/{train,val,test}/...
```

- Detection boxes: `class cx cy w h` normalized to [0, 1]; optional trailing
  confidence for prediction exports.
- Masks/segmentation: `class x1 y1 x2 y2 ...` (even coordinate count, ≥ 3
  points).
- An empty label file is a valid negative sample; an empty or corrupt image
  is an error.

## Validation and checksums

From `backend/`:

```bash
python -m app.datasets.cli ../datasets/<name> \
  --report ../datasets/<name>.validation.json \
  --num-classes <N> --seed 42 --ratios 0.7,0.2,0.1 --checksums
```

- Exit code `0` = no errors, `1` = validation errors found, `2` = bad usage.
- The JSON report contains pairing/class/box/mask/corrupt-file issues,
  deterministic split assignments grouped by source identity (no leakage by
  construction), duplicate/leakage findings, SHA-256 checksums per file, and a
  stable dataset `fingerprint` used as training-run identity.
- Keep the report file; the training and evaluation entry points reference
  its fingerprint so metrics are always tied to an identified dataset split.

## Known limitations

- This guide documents license *statements found on provider pages*, with the
  date of verification; providers can change terms — re-check before use.
- No third-party dataset or annotation file is executed/validated in this
  workspace's tests; all automated tests use synthetic fixtures, which are
  not industrial data.
