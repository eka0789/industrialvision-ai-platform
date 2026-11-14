# Model Cards — PdM / RAG components

Honest model documentation per the no-fabricated-numbers rule: metrics below
exist only when a completed run produced them in this workspace; sample-count
rules apply. Components without a completed run state that explicitly.

## 1. PdM failure classifier (`failure_classification`)

- **Task:** binary machine-failure classification from 5 sensor features
  (AI4I 2020 schema: air/process temperature, rotational speed, torque, tool
  wear).
- **Training data:** `synthetic-mirror` of AI4I 2020 generated deterministically
  from the run seed (`app/pdm/dataset.py`, documented generative thresholds).
  **Not real fleet data.** If a real AI4I-format CSV is placed at
  `PDM_DATASET_CSV`, the pipeline uses it verbatim (kind becomes `real-csv`).
- **Candidate selection:** ≥3 sklearn models (logistic regression / random
  forest / gradient boosting) compared on the VALIDATION split (F1); reported
  metrics come only from the held-out test split. Exact figures are recorded
  per run in the run manifest (`data/pdm/training_runs/<run_id>/manifest.json`)
  and mirrored into the registry `provenance_json` — never inline in docs.
- **Intended use:** decision support for maintenance scheduling. Fail-safe
  contract: `model` / `rules-only-fallback` / `error` modes are explicit in
  every API response.
- **Limitations:** synthetic mirror (class balance follows the generator, not
  a real fleet); no product-grade context in rules mode; thresholds documented
  in `app/pdm/dataset.py`.

## 2. PdM RUL regressor (`rul_regression`)

- **Task:** remaining-useful-life (minutes) estimate over synthetic degradation
  trajectories (tool-wear-to-threshold model, documented in
  `app/pdm/train.py::synthesize_rul_series`).
- **Selection:** ≥3 candidates on validation MAE; test MAE from the held-out
  split only, recorded in the run manifest.
- **Limitations:** the degradation model is a documented assumption, not
  measured fleet physics; RUL is best-effort and `null` when no active
  regressor exists (never blocks the failure-mode answer).

## 3. RAG embedding — `local-hashing` (fallback)

- **Task:** deterministic text embedding, dim 384, unigram+bigram feature
  hashing with sign trick, L2-normalized.
- **Properties:** deterministic, dependency-free, labeled in every response.
- **Limitations:** NOT a semantic model — supports keyword-adjacent recall
  only. Semantic quality requires OpenAI (key) or sentence-transformers
  (optional extra); the active provider is always self-reported.

## 4. RAG answerer — `mock-demo` (fallback)

- **Task:** extractive, deterministic answers from retrieved chunks with
  enforced 1:1 citations.
- **Limitations:** no generation/abstraction — it echoes grounded sentences.
  `openai`/`gemini` paths activate only with configured keys; refusals are
  produced whenever retrieval is empty (test-enforced).

## 5. Anomaly memory banks (PatchCore / PaDiM)

- **Task:** memory-bank / embedding-statistics training from NORMAL samples
  via the existing training-run lifecycle.
- **Status in this workspace:** the compatibility gate decides; with the
  optional torch runtime absent the run is marked `failed` with the reason and
  no metrics (tested). No memory bank is claimed as trained here.
- **Limitations:** lightweight fallback features (32×32) when torch is absent;
  real anomaly quality requires the documented datasets (docs/DATASETS.md).
