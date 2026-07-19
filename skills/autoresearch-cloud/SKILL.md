---
name: autoresearch-cloud
description: Run a sourced, budgeted autoresearch campaign on one persistent cloud GPU using Linkup web search and Hermes.
---

# Cloud autoresearch loop

Use this skill only inside a dedicated autoresearch checkout on a cloud worker. The worker must
have one NVIDIA GPU, `git`, `uv`, persistent cache/results mounts, and a clean experimental branch.
Only `train.py` may change during experiments. Never modify `prepare.py`, the evaluation metric,
the dataset, or project dependencies.

## Required environment

- `LINKUP_API_KEY`: Linkup credential. Keep it in the Hermes or worker secret store, never Git.
- `AUTORESEARCH_CACHE_DIR`: persistent dataset/tokenizer directory.
- `AUTORESEARCH_RESULTS_DIR`: persistent experiment results directory.

Use the same exact GPU model for the entire campaign. Results from different GPU types are not
comparable because the experiment budget is fixed by wall-clock time.

## Start a campaign

1. Confirm the objective, maximum experiment count, maximum cloud spend, GPU type, and minimum
   meaningful `val_bpb` improvement.
2. Create a dedicated branch from the intended base branch. Do not use a checkout containing user
   work or unrelated changes.
3. Run `uv sync --frozen` and `uv run --frozen prepare.py` once against the persistent cache.
4. Run a smoke test:

   ```bash
   uv run --frozen python -m cloud.experiment \
     --id setup-smoke --description "cloud setup smoke test" --smoke-test
   ```

5. Run and preserve the baseline:

   ```bash
   uv run --frozen python -m cloud.experiment \
     --id baseline --description "unmodified cloud baseline" --objective "$OBJECTIVE"
   ```

The baseline result is `$AUTORESEARCH_RESULTS_DIR/baseline/result.json`.

## Generate sourced candidates

Search must produce experiments, not a literature summary:

```bash
uv run --frozen python -m cloud.websearch \
  --objective "$OBJECTIVE" --hardware "$HARDWARE" \
  --count 5 --depth deep --output queue/candidates.json
```

Read the packet and reject any candidate when its source does not support the claimed mechanism,
it violates the five-minute/single-GPU contract, it duplicates a completed experiment, or its
minimal test requires changes outside `train.py`.

Rank the remaining candidates by evidence quality, expected information gain, implementation
simplicity, compatibility, and GPU cost. Do not rank by novelty alone.

## Execute one candidate

1. Record the current commit as the candidate base.
2. Implement one isolated change in `train.py`.
3. Inspect the diff. Revert scope creep before running anything.
4. Run Python syntax checks and the cloud experiment smoke test.
5. Commit the candidate so every result points to an immutable Git revision.
6. Run the full experiment:

   ```bash
   uv run --frozen python -m cloud.experiment \
     --id "$EXPERIMENT_ID" \
     --description "$DESCRIPTION" \
     --objective "$OBJECTIVE" \
     --research-packet queue/candidates.json \
     --candidate-id "$CANDIDATE_ID" \
     --baseline-result "$AUTORESEARCH_RESULTS_DIR/baseline/result.json" \
     --min-improvement 0.001
   ```

7. Read `result.json`, not the full training log unless the verdict is `crash`, `invalid`, or
   `timeout`.
8. Keep an improved candidate. For a rejected candidate, use `git revert` on its isolated commit;
   do not erase the result record.

Do not automatically push every experiment. Push only reviewed milestones.

## Let failure drive the next search

After a crash or non-improvement, do not merely ask for another generic idea. Search for the
specific failure mode, including the relevant model scale, five-minute horizon, optimizer or
architecture involved, and observed result. Generate a new packet from that narrower question.
This is the closed loop: measured failures change the next web search.

## Reproduction gate

Before declaring a winner:

1. Repeat the candidate enough times to distinguish a real improvement from run variance.
2. Compare on the same GPU type and software lockfile.
3. Reject a candidate whose improvement disappears, whose memory use becomes unsafe, or whose
   complexity is disproportionate to the gain.
4. Preserve source URLs, Git commit, GPU metadata, logs, result JSON, and the final decision.

## Stop conditions

Stop immediately when the experiment count or spend ceiling is reached, the GPU changes, the
baseline becomes invalid, credentials fail repeatedly, or persistent storage is unavailable.
Report the best reproduced result, total experiments, total GPU time, failures, retained commit,
and unresolved risks through the configured Hermes delivery channel.
