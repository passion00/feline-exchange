# Portable local AI

## v0.17.6 experimental Qwen3.5 candidate

The catalog retains `qwen3-4b-q4km` as default and adds selectable `qwen35-4b-q5km`. The candidate is Unsloth's Q5_K_M conversion of official `Qwen/Qwen3.5-4B`, pinned at repository revision `e87f176479d0855a907a41277aca2f8ee7a09523`, 3,143,656,608 bytes and SHA-256 `8814232b85594dcd46c50e5b8b29324a7efe9e746edbe8a3d1df3d3fce7aad39`. This reputable third-party conversion is used because Qwen does not publish the requested official GGUF. Pinned llama.cpp b9637 supports `qwen35`, JSON Schema, seed and configured samplers, so no runtime upgrade was needed.

Install with `python3 -m feline ai select-model qwen35-4b-q5km` followed by `python3 -m feline ai install-model --yes`. Experiments can use `--model-id` without changing the saved preference. Qwen3.5 uses chat-template `enable_thinking`; it does not support Qwen3's `/think` soft switch. Feline retains an 8,192-token operational CPU context on 16 GB hosts.

The v0.17.6 CPU bake-off found b9637/Qwen3.5 non-thinking output reliable, but thinking consumed the complete 2,048- and 4,096-token budgets without producing final JSON. The entry remains experimental and is not the default. This is a model/runtime operational limitation, not permission to parse hidden reasoning or relax validation.

## Qwen thinking policy

The managed b9637 server starts purpose-neutral. News requests enable Qwen thinking; signal assessment disables it. Managed requests explicitly carry sampler parameters and the experiment seed. Seeds improve repeatability, though runtime scheduling and provider implementation may prevent byte-identical generations.

Evaluate an installed managed-local model without changing trading configuration using `python3 -m feline experiment news-intelligence --suite smoke --ai local --start-ai`. It never downloads assets implicitly or contacts an external broker. See [Experiments](EXPERIMENTS.md).

Thinking news analysis defaults to a 900-second deadline because Qwen3 4B CPU inference can take minutes. This is normal busy work, not an offline state. The GUI shows the active headline and elapsed time while analyzing. The pinned llama.cpp b9637 server runs purpose-neutral (`auto`); each managed request uses the Qwen chat-template thinking flag. Short signal assessment remains non-thinking.

Feline v0.17.1 owns the optional local AI installation location and process lifecycle. Missing AI assets never prevent replay, market ingestion, deterministic risk, PaperBroker, broker monitoring, or news ingestion from running.

## Layout and first run

Tracked metadata lives in `feline/resources/ai_catalog.toml`. Large machine-local assets live under:

```text
models/
runtime/llama.cpp/install/
```

Those binaries, archives, partial downloads, and staging directories are ignored by Git. Startup and `feline doctor` inspect the layout but never silently download assets. Explicit installation is:

```bash
python3 -m feline ai list-models
python3 -m feline ai install --yes
python3 -m feline ai start-local
python3 -m feline ai status
python3 -m feline ai stop-local
```

The catalog default is the official `Qwen/Qwen3-4B-GGUF` `Qwen3-4B-Q4_K_M.gguf`: 4B parameters, Q4_K_M, approximately 2.33 GiB on disk, and roughly 6 GB RAM recommended. The pinned Linux x86-64 runtime is official `ggml-org/llama.cpp` release `b9637`, CPU archive `llama-b9637-bin-ubuntu-x64.tar.gz`. The catalog stores the upstream artifact SHA-256 values and stable Feline API alias separately from physical filenames.

## Integrity and recovery

Downloads require HTTPS. Models download to `*.gguf.part`, support HTTP Range resume, verify expected byte count and SHA-256, and are atomically renamed only after success. A server that ignores Range causes a clean restart rather than corrupt append. Runtime archives are pinned and checksum-verified, extracted into staging with traversal/link checks, and atomically promoted only after exactly one `llama-server` is found. Interrupted partials remain recognizable and resumable; no partial executable is run.

The initial model hash comes from the official Hugging Face linked ETag for the immutable artifact revision recorded during release engineering. The runtime hash comes from the official GitHub release asset digest. Updating either pinned artifact requires deliberate catalog review.

## Selection, hardware, and existing assets

Catalog selection is persisted in ignored `data/ai_preferences.json`:

```bash
python3 -m feline ai select-model qwen3-4b-q4km
```

Custom models remain supported without copying multi-gigabyte files:

```bash
python3 -m feline ai import-model /path/to/model.gguf
# Add --copy only when an actual repository-local copy is desired.
```

Symlinks work. `custom_model_path` and `llama_server_executable` remain explicit compatibility overrides. Feline detects OS, architecture, RAM, NVIDIA presence/VRAM when available, and Apple Silicon. This produces recommendations and warnings only; an explicit selection is never silently replaced. Very low-memory systems receive a warning rather than an undocumented fallback.

Model changes do not hot-swap a running server. Stop and explicitly restart local AI after selection. News collection and deterministic trading infrastructure continue while AI is stopped or restarting.

To reinstall, first run `python3 -m feline ai stop-local`, then remove only the ignored `runtime/llama.cpp/install/` directory (or the selected ignored GGUF/`.part` file) and rerun `ai install --yes`. Never remove the tracked README or manifest. This deliberately remains an explicit filesystem operation rather than a broad destructive CLI command.

## External endpoints and offline use

Set `provider = "openai_compatible"` and configure `base_url`/`model` to use an externally managed compatible endpoint. Local runtime/model assets are then not required and Feline never manages that process. Managed local mode binds the configured host/port using argument arrays, never a shell command. A port conflict is reported; Feline never kills an unknown listener. It stops only the PID whose executable and model match its own atomic PID record.

The GUI AI Manager can persist managed-local versus external provider, endpoint, and API-model alias preferences in the same ignored preference file. Applying a preference never hot-swaps a running server or changes broker/trading state; restart AI work explicitly.

Managed settings under `[ai]` are `model_id` (optional catalog lock), `models_directory`, `runtime_directory`, `preference_path`, `custom_model_path`, `llama_server_executable`, `context_size`, optional `threads`/`gpu_layers`, `startup_timeout_seconds`, and `reasoning_mode`. Relative asset paths resolve from the Feline installation root, not the caller's working directory. Legacy `provider = "llama_cpp"`, `local_model_path`, and executable overrides remain readable. External endpoint mode continues to use `base_url`, `model`, timeouts, retries, and the existing AI decision/purpose modes.

Without internet, installation reports a bounded download error. All non-AI functions remain usable, and AI/news-thesis jobs retain their existing fail-safe unavailable/NO_TRADE behavior.

The default model and synthetic/research workflows carry no quality, trading, or profitability guarantee.

Managed-local news jobs use b9637's verified OpenAI-compatible `response_format` JSON Schema support to constrain generation to the Feline contract and current instrument universe. This is not sent blindly to external endpoints, whose feature support may differ. Deterministic validation remains authoritative in every mode.
