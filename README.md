# llm-harness

Shared config for running OpenCode and Codex CLI against our self-hosted model,
with an OpenAI dry-run target that exercises the same code path.

| Target | Endpoint | Used for |
| --- | --- | --- |
| `local` (default) | vLLM via SSH tunnel, `LOCAL_BASE_URL` | The real thing |
| `dryrun` | `https://api.openai.com/v1` | Testing config, permissions and tooling before the server exists |

OpenCode talks Chat Completions; Codex talks the Responses API (it no longer
supports Chat Completions for custom providers). vLLM serves both, so the smoke
test checks both.

The OpenCode `dryrun` provider uses OpenAI's own adapter (`@ai-sdk/openai`) rather
than the generic one used for vLLM: the generic adapter sends `max_tokens`, which
OpenAI's reasoning models reject. So an OpenCode dry run tests the harness, not
the vLLM adapter; the smoke test covers that.

## Setup

```bash
scripts/setup.sh          # installs pinned opencode + codex, creates .env
$EDITOR .env              # OPENAI_API_KEY (platform key), LOCAL_API_KEY
```

## Use

```bash
scripts/smoke-test.sh dryrun      # one tool call on each API; expect PASS twice
bin/oc dryrun                     # OpenCode TUI against OpenAI
bin/cx dryrun                     # Codex against OpenAI

ssh -N -L 8000:localhost:8000 root@<pod-ip> -p <port> -i ~/.ssh/<key>   # in another terminal
scripts/smoke-test.sh             # same checks against vLLM
bin/oc                            # OpenCode against our model
bin/cx                            # Codex against our model
```

Run them from inside the project you want the agent to work on, e.g.
`cd ~/git/some-repo && ~/llm-harness/bin/oc dryrun`.

## What the launchers lock down

- **OpenCode:** only our two providers are enabled; sharing, auto-update and the
  models.dev catalogue fetch are off; the title-generation model is our own model.
  Edits, shell commands and web fetches prompt, except read-only git/ls; web search is denied.
- **Codex:** runs with its own `CODEX_HOME` (`.codex-home/`, gitignored), so your
  personal ChatGPT login, history and memories are never used. Analytics, update
  checks and OpenAI's hosted web search are off; workspace-write sandbox, approval on request.

These are settings in the software, not guarantees. Enforce no-egress at the
firewall or container level.

## Running the model on RunPod

### Pod

- **Template:** RunPod PyTorch (the CUDA 12.8 one works with the extra step below),
  not the vLLM template. The vLLM template auto-starts a default gated model
  (crash-loops without an HF token), only offers `vllm-latest`, and runs no sshd,
  so SSH port forwarding doesn't work.
- Secure Cloud, EU data center, 1x RTX PRO 6000 to start. Container disk ~80 GB.
- **Global volume** for model weights only (no repos, logs or tokens). It's
  mounted at `/workspace` as an object-storage-backed FUSE filesystem (`geesefs`,
  shows as 1.0P in `df`). It does not allow `chmod`, symlinks and file locks are
  unreliable, and access is billed per IOPS, so keep caches and venvs on local disk.
  Check on each new pod: `df -h /workspace` should show the volume, not `overlay`.
- Expose TCP 22 only. Never expose port 8000 over HTTP: the HTTPS proxy terminates
  TLS on RunPod's side.
- Connect with "SSH over exposed TCP" (`root@<ip> -p <port>`), not `ssh.runpod.io`,
  which doesn't support port forwarding.
- Stop the pod when done; the volume keeps billing until it's deleted.

### Install vLLM (on every new pod, a few minutes)

```bash
export UV_CACHE_DIR=/root/.cache/uv XDG_CACHE_HOME=/root/.cache   # off /workspace
pip install uv
uv venv /root/venv --python 3.12 && . /root/venv/bin/activate
uv pip install "vllm==0.30.0"     # pinned; Qwen3.8 needs >= 0.17.0

# vLLM 0.30.0 ships PyTorch built for CUDA 13.0, but the template's nvcc is 12.8,
# which can't build FlashInfer's FP8 KV-cache kernels for the RTX PRO 6000 (sm_120).
# Install a matching CUDA 13 toolkit (~5 min). Skip if `nvcc --version` already says 13.x.
apt-get update && apt-get install -y cuda-toolkit-13-0
```

### Download weights (once, onto the volume)

Plain folders instead of the HF cache, which needs symlinks and locks:

```bash
hf download Qwen/Qwen3.8-27B-FP8 --local-dir /workspace/models/qwen3.8-27b-fp8
```

The repeated "Could not set the permissions ... Continuing without setting
permissions" warnings are harmless (the volume has no permissions). Expect about
30 min for 31 GB; set `HF_TOKEN` (free account) for higher rate limits.

| Model | Repo | Size |
| --- | --- | --- |
| Qwen3.8-27B FP8 | `Qwen/Qwen3.8-27B-FP8` | 31 GB |
| Qwen3.8-27B NVFP4 | `nvidia/Qwen3.8-27B-NVFP4` | 22 GB |

### Serve (in tmux)

`--served-model-name` must match `LOCAL_MODEL`, and `VLLM_KEY` must match
`LOCAL_API_KEY` (generate with `openssl rand -hex 32`). Set the key in the
SSH shell, not in the RunPod template's env vars, which the web console shows.

```bash
. /root/venv/bin/activate
export VLLM_CACHE_ROOT=/root/.cache/vllm VLLM_NO_USAGE_STATS=1 HF_HUB_OFFLINE=1
export CUDA_HOME=/usr/local/cuda-13.0 PATH=/usr/local/cuda-13.0/bin:$PATH
export VLLM_KEY=<your key>

vllm serve /workspace/models/qwen3.8-27b-fp8 \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.92 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Startup takes a few minutes; it's ready at `Application startup complete`. Then
pull the numbers worth recording out of the log (one line):

```bash
grep -E 'Available KV cache memory|GPU KV cache size|Maximum concurrency|Model loading took|Loading weights took|[Aa]ttention backend|Using .* backend|startup complete' /root/vllm.log | grep -v Initializing | cut -c1-200
```

- If the smoke test fails on `/chat/completions`, try `--tool-call-parser qwen3_coder`.
  If only `/responses` fails, that's vLLM's Responses API: OpenCode works, Codex won't.
- Record load time (global-volume speed), GPU KV cache size (4 agents x 131k needs
  ~524k tokens) and max concurrency at 131k (want >= 4x).
- During agent runs, `Prefix cache hit rate` in the log should climb on later turns.
- If loading weights from `/workspace` is slow, copy the model folder to local disk
  first (`cp -r /workspace/models/qwen3.8-27b-fp8 /root/models/`) and serve from there.
  This only works while the model fits on the container disk.

### Troubleshooting

- **SSH drops immediately with "container ... is not running":** the container is
  crash-looping (usually the start command). Stop the pod and check its logs.
- **`Operation not permitted` / `failed to set permissions` during install:** a
  cache or venv is on `/workspace`. Set `UV_CACHE_DIR`/`XDG_CACHE_HOME` as above.
- **`RuntimeError: No supported CUDA architectures found for major versions [9, 10, 12]`:**
  FlashInfer is compiling with the template's CUDA 12.8 `nvcc`. Install the CUDA 13
  toolkit and set `CUDA_HOME`/`PATH` as above, in the same shell that starts vLLM.
- **`channel N: open failed: connect failed: Connection refused` in the tunnel:**
  something used the tunnel before vLLM was listening. Wait for startup; the tunnel is fine.
- **OpenCode hangs silently:** the tunnel is probably down; it retries without
  printing an error.

Sources: [model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).
