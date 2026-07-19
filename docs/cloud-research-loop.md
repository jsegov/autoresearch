# Cloud research loop

This experimental workflow keeps the expensive GPU separate from the research control plane:

```text
Hermes + Linkup web search -> sourced experiment packet -> cloud GPU worker
           ^                                             |
           |---------- result JSON + failure signal -----|
```

Linkup is the evidence and hypothesis layer. Hermes is the persistent research lead. The cloud
worker is a deliberately narrow laboratory: it runs the unchanged evaluation contract, records
the environment, and returns an immutable result. Web search must change what is tested next;
Hermes must not be reduced to a chat or webhook wrapper.

## Deployment shape

For the first campaign, use one Linux cloud instance with a dedicated consumer NVIDIA GPU such as
an RTX 4090 and persistent storage. Keep Hermes on a CPU machine and point its terminal backend at
the worker over SSH so the research state survives worker replacement. A persistent GPU instance
still bills while it is idle. To eliminate idle GPU charges, invoke `cloud.experiment` through a
provider job or serverless wrapper that mounts the same cache and results volumes; provider
provisioning is intentionally outside this repository.

Do not switch GPU models during a campaign. The five-minute training budget makes results specific
to the selected hardware.

## Worker setup

The worker needs Python 3.10+, `uv`, `git`, an NVIDIA driver visible through `nvidia-smi`, and enough
persistent storage for the dataset and result ledger.

```bash
git clone https://github.com/OWNER/autoresearch-win-rtx.git
cd autoresearch-win-rtx

export AUTORESEARCH_CACHE_DIR=/workspace/autoresearch-cache
export AUTORESEARCH_RESULTS_DIR=/workspace/autoresearch-results
export LINKUP_API_KEY=stored-in-your-secret-manager

uv sync --frozen
uv run --frozen prepare.py
uv run --frozen python -m cloud.experiment \
  --id setup-smoke --description "cloud setup smoke test" --smoke-test
```

The cache and results paths should live on a persistent volume. The experiment runner places its
checkpoint inside the ignored result directory rather than dirtying the repository root.

## Hermes setup

Add this repository's `skills` directory to Hermes' external skill directories:

```yaml
skills:
  external_dirs:
    - /workspace/autoresearch-win-rtx/skills
```

If Hermes is outside the GPU worker, configure its SSH terminal backend with the worker host, user,
and key. Load `autoresearch-cloud` in a fresh Hermes session and give it an explicit objective,
experiment ceiling, spend ceiling, GPU type, and minimum meaningful improvement.

## Manual contract

Generate candidates:

```bash
uv run --frozen python -m cloud.websearch \
  --objective "Lower TinyStories validation BPB without exceeding 24 GB VRAM" \
  --hardware "one RTX 4090 with 24 GB VRAM" \
  --count 5 --depth deep --output queue/candidates.json
```

Run the baseline:

```bash
uv run --frozen python -m cloud.experiment \
  --id baseline --description "unmodified cloud baseline"
```

After implementing and committing one candidate in `train.py`, run it against the baseline:

```bash
uv run --frozen python -m cloud.experiment \
  --id warmup-01 \
  --description "shorter warmup from sourced candidate" \
  --research-packet queue/candidates.json \
  --candidate-id CANDIDATE_ID \
  --baseline-result "$AUTORESEARCH_RESULTS_DIR/baseline/result.json" \
  --min-improvement 0.001
```

Each experiment writes:

```text
$AUTORESEARCH_RESULTS_DIR/
├── ledger.jsonl
├── baseline/
│   ├── checkpoint_pre_eval.pt
│   ├── result.json
│   └── run.log
└── warmup-01/
    ├── checkpoint_pre_eval.pt
    ├── result.json
    └── run.log
```

The runner never edits Git state or decides what to revert. Hermes makes that decision from the
structured verdict and preserves rejected attempts with `git revert`.

## Secrets and spending

- Never place `LINKUP_API_KEY`, cloud credentials, SSH keys, or provider tokens in Git.
- Store credentials in the cloud provider or Hermes secret store.
- Set a provider-side spending alert in addition to the campaign's experiment ceiling.
- Stop or destroy the GPU instance when the campaign finishes; persistent storage bills
  independently from compute.
- Treat source-backed hypotheses as proposals, not truth. Reproduction on the campaign hardware is
  the acceptance gate.
