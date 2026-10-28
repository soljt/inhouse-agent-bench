# Serving the model on RunPod

How the self-hosted models behind the `local` target were served: pod choice, pod checks, vLLM install,
weights and the serve command per model. Measured server reports are in [`../results/`](../results/); the
benchmark itself is described in the [README](../README.md).


## Pod

- **Template:** RunPod PyTorch (the CUDA 12.8 one works with the extra step below),
  not the vLLM template. The vLLM template auto-starts a default gated model
  (crash-loops without an HF token), only offers `vllm-latest`, and runs no sshd,
  so SSH port forwarding doesn't work.
- Secure Cloud, EU data center, sized per model:

  | Model | GPUs | Container disk | Host RAM |
  | --- | --- | --- | --- |
  | Qwen3.8-27B FP8 | 1x RTX PRO 6000 | ~80 GB | any |
  | Qwen3.8-27B BF16 | 1x RTX PRO 6000 | ~100 GB | any |
  | Qwen3.8-Flash-Next FP8 | 2x RTX PRO 6000 (tight) or 4x (comfortable), one machine | ~220 GB | 128 GB or more: its 51 GB lookup table lives in RAM |
  | Quantization comparison: Flash-Next NVFP4, then 27B BF16 | 1x RTX PRO 6000 | ~250 GB, one model on disk at a time | More than 125 GB (`memory.max`, see below): this checkpoint's lookup table is BF16, 102 GB |
  | GLM-5.3-Flash FP8 | 4x RTX PRO 6000, one machine | ~400 GB | any |

- **No volume.** Everything, weights included, lives on the container disk: downloading straight from
  Hugging Face to it is fast, so there is nothing worth keeping between pods. The container disk is wiped
  whenever the pod stops or restarts, including when you change its disk size, so every start begins with
  the steps below.
- Expose TCP 22 only. Never expose port 8000 over HTTP: the HTTPS proxy terminates
  TLS on RunPod's side.
- Connect with "SSH over exposed TCP" (`root@<ip> -p <port>`), not `ssh.runpod.io`,
  which doesn't support port forwarding. The port changes when the pod restarts.
- Stop or terminate the pod when done; either way nothing on it survives.

## Check the pod (every start)

```bash
nvidia-smi --query-gpu=index,name,memory.total --format=csv
cat /sys/fs/cgroup/memory.max
df -h /root
```

Expect one line per GPU you ordered, each `NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB`, and RAM
and disk as in the table above. `memory.max` is the pod's RAM limit in bytes. `free -g` shows the whole host's
RAM instead (1 TB on one 1x RTX PRO 6000 pod whose limit was 125 GB), so it is no guide to what the pod can use.

With several GPUs, also check how they are connected. The model is split across them, and they exchange
partial results after every layer; RTX PRO 6000s have no NVLink, so that goes over PCIe:

```bash
nvidia-smi topo -m          # the entry between each pair of GPUs
nvidia-smi topo -p2p r      # whether they can copy to each other directly
```

`topo -m` prints a matrix like this one (2 GPUs on one PCIe switch):

```
        GPU0    GPU1    CPU Affinity    NUMA Affinity
GPU0     X      PIX     0-47            0
GPU1    PIX      X      0-47            0
```

| Entry | Means | Verdict |
| --- | --- | --- |
| `PIX`, `PXB` | Same PCIe switch | Best |
| `NODE` | Same CPU, different PCIe root ports | Fine |
| `SYS` | Different CPU sockets: traffic crosses between them | Slow: redeploy for another machine |

`topo -p2p r` should show `OK` between every pair. `NS` (not supported) still works, but the GPUs then copy
through host memory, which is slower; see Troubleshooting if startup hangs.

`OK` only means the driver says the GPUs support direct copies, not that the data arrives. On 2026-10-14 a
2-GPU pod and a 4-GPU pod both showed `OK` everywhere, yet direct copies came back with the wrong contents
(on the 4-GPU pod, every copy from a lower to a higher numbered GPU), and vLLM hung at startup. Neither host
had `iommu=pt` in its kernel settings, so the IOMMU most likely blocks the copies; a rented pod can't change
that. So check before installing anything (the PyTorch template has torch already):

```bash
cat /proc/cmdline       # the host's kernel settings: iommu=pt is a good sign, nothing about iommu a warning
```

Then copy something between every pair of GPUs and compare:

```bash
python - <<'EOF'
import itertools, torch
x = torch.randn(64 * 1024 * 1024)                          # 256 MB
for a, b in itertools.permutations(range(torch.cuda.device_count()), 2):
    copied = x.to(f'cuda:{a}').to(f'cuda:{b}').cpu()
    print(f'GPU{a} -> GPU{b}:', 'OK' if torch.equal(copied, x) else 'WRONG DATA')
EOF
```

Expect `OK` for every pair. Any `WRONG DATA` (or a hang) means the host can't do direct copies, and what to do
depends on what the pod is for:

- **Load tests, or anything else that measures speed:** terminate the pod and deploy another (stopping and
  starting it may land on the same host). Without direct copies the GPUs exchange data through host memory,
  which is slower (a 4x RTX PRO 6000 GLM-5.3-Flash run read prompts 51 to 59% faster with them), and a server
  you own would have them, so speed measured without them understates what you'd get. If several redeploys
  fail, ask RunPod support for a host with working peer-to-peer copies.
- **Quality runs only (`bench/suite.py`):** the pod can stay, if NCCL works without direct copies. Check:

  ```bash
  cat > /root/nccl_test.py <<'PY'
  import torch, torch.distributed as dist, torch.multiprocessing as mp

  def run(rank, n):
      dist.init_process_group('nccl', init_method='tcp://127.0.0.1:29511', rank=rank, world_size=n)
      torch.cuda.set_device(rank)
      x = torch.full((64 * 1024 * 1024,), float(rank + 1), device='cuda')
      dist.all_reduce(x)
      torch.cuda.synchronize()
      print(f'rank {rank}:', 'OK' if bool((x == n * (n + 1) / 2).all()) else 'WRONG DATA', flush=True)
      dist.destroy_process_group()

  if __name__ == '__main__':
      n = torch.cuda.device_count()
      mp.spawn(run, args=(n,), nprocs=n)
  PY
  NCCL_P2P_DISABLE=1 timeout 120 python /root/nccl_test.py; echo "exit: $?"
  ```

  Every rank `OK` and `exit: 0`: start the server with `export NCCL_P2P_DISABLE=1` and
  `--disable-custom-all-reduce` (vLLM's own all-reduce copies directly and ignores the variable). vLLM sends
  data between the GPUs only through these two, so the answers are the same as on a host with direct copies;
  only the speed differs. Leave `BENCH_VARIANT` unset so the runs join the model's report row, and don't run
  load tests on this pod. (On the 4-GPU pod above, this test passed with `NCCL_P2P_DISABLE=1` and hung without.)

## Install vLLM (on every pod start, a few minutes)

```bash
export UV_CACHE_DIR=/root/.cache/uv XDG_CACHE_HOME=/root/.cache
pip install uv
uv venv /root/venv --python 3.12 && . /root/venv/bin/activate
uv pip install "vllm==0.30.0"     # pinned; Qwen3.8-27B needs >= 0.17.0, Flash-Next >= 0.30.0
# uv pip install "vllm==0.31.0"   # instead, for GLM-5.3-Flash (needs FlashInfer >= 0.6.18, see below)

# vLLM 0.30.0 ships PyTorch built for CUDA 13.0, but the template's nvcc is 12.8,
# which can't build FlashInfer's FP8 KV-cache kernels for the RTX PRO 6000 (sm_120).
# Install a matching CUDA 13 toolkit (~5 min). Skip if `nvcc --version` already says 13.x.
apt-get update && apt-get install -y cuda-toolkit-13-0
```

Flash-Next runs on stable vLLM 0.30.0 and later, but its [recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)
is tuned and tested on nightly builds; if the pinned version fails to load it, the recipe's Install section
generates the nightly install command.

GLM-5.3-Flash needs vLLM 0.31.0 (the version a published 4x RTX PRO 6000 run used) and FlashInfer 0.6.18 or
newer, for its sparse attention. Check with `python -c "import flashinfer; print(flashinfer.__version__)"`
after the install, and `uv pip install -U flashinfer-python` if it is older.

## Download weights (on every pod start, to the container disk)

Log in first (free account, read token from huggingface.co/settings/tokens) for higher rate limits. Type the
token into the SSH shell, never into the RunPod template's env vars, which the web console shows.

```bash
. /root/venv/bin/activate                  # the hf CLI comes with vLLM
hf auth login                              # paste the token; answer "n" to the git credential question
export HF_XET_HIGH_PERFORMANCE=1
hf download Qwen/Qwen3.8-27B-FP8 --local-dir /root/models/qwen3.8-27b-fp8
hf download Qwen/Qwen3.8-Flash-Next-FP8 --local-dir /root/models/qwen3.8-flash-next-fp8   # instead, for Flash-Next
hf download zai-org/GLM-5.3-Flash --local-dir /root/models/glm-5.3-flash-fp8               # instead, for GLM-5.3-Flash

# Quantization comparison (see below): 1x RTX PRO 6000, one model at a time
hf download Inferact/Qwen3.8-Flash-Next-NVFP4 --local-dir /root/models/qwen3.8-flash-next-nvfp4
hf download Qwen/Qwen3.8-27B --local-dir /root/models/qwen3.8-27b-bf16
```

Several models can share one pod if each is deleted before the next is downloaded. A 250 GB container disk
then suffices for the quantization comparison: the largest model (Flash-Next NVFP4, 183 GB) plus the venv, the
CUDA 13 toolkit and the compile caches (about 25 GB), with room to spare. It pays to serve the largest or least
tested model first, while the pod is fresh and any troubleshooting doesn't hold up the rest.

```bash
rm -rf /root/models/qwen3.8-flash-next-nvfp4    # a model that is no longer needed
rm -rf /root/.cache/huggingface/xet             # the download cache, if present
df -h /root
```

Expect a few minutes even for big models: Qwen3.8-Flash-Next FP8 (186 GB) took about 3 min, peaking around
6 GB/s. An interrupted download resumes when you run the same command again.

| Model | Repo | Size | Serving |
| --- | --- | --- | --- |
| Qwen3.8-27B FP8 | `Qwen/Qwen3.8-27B-FP8` | 31 GB | 1x RTX PRO 6000, below |
| Qwen3.8-27B BF16 | `Qwen/Qwen3.8-27B` | 56 GB | 1x RTX PRO 6000, below |
| Qwen3.8-27B NVFP4 | `nvidia/Qwen3.8-27B-NVFP4` | 22 GB | 1x RTX PRO 6000 (untested) |
| Qwen3.8-Flash-Next FP8 | `Qwen/Qwen3.8-Flash-Next-FP8` | 186 GB | 2 or 4x RTX PRO 6000, below |
| Qwen3.8-Flash-Next NVFP4 | `Inferact/Qwen3.8-Flash-Next-NVFP4` | 183 GB, of which 80 GB go on the GPU | 1x RTX PRO 6000, below |
| GLM-5.3-Flash FP8 | `zai-org/GLM-5.3-Flash` | 328 GB | 4x RTX PRO 6000, below |
| GLM-5.3-Flash NVFP4 | `RedHatAI/GLM-5.3-Flash-NVFP4` | ~200 GB | 4x RTX PRO 6000, fallback if FP8 doesn't fit |

## Serve: common to every model

`--served-model-name` must match `LOCAL_MODEL` in `.env`, and `VLLM_KEY` must match
`LOCAL_API_KEY` (generate with `openssl rand -hex 32`). Set the key in the SSH
shell, not in the RunPod template's env vars. The model name also goes into every run folder's name, so each
model's runs and report rows stay apart. Start the server in tmux, after this in the same shell:

```bash
. /root/venv/bin/activate
export VLLM_CACHE_ROOT=/root/.cache/vllm VLLM_NO_USAGE_STATS=1 HF_HUB_OFFLINE=1
export CUDA_HOME=/usr/local/cuda-13.0 PATH=/usr/local/cuda-13.0/bin:$PATH
export VLLM_KEY=<your key>
```

It's ready at `Application startup complete`. Then pull the numbers worth recording out of the log:

```bash
grep -E 'Available KV cache memory|GPU KV cache size|Maximum concurrency|Model loading took|Loading weights took|[Aa]ttention backend|Using .* backend|startup complete' /root/vllm.log | grep -v Initializing | cut -c1-200
```

Then, on your machine: the SSH tunnel (see Use), `scripts/smoke-test.sh` (expect PASS twice) and
`bench/suite.py --dry-run` to see what a suite would run for this model.

## Serve: Qwen3.8-27B on 1 GPU

```bash
vllm serve /root/models/qwen3.8-27b-fp8 \
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

Expect, from the first serve (`results/2026-10-07_qwen3.8-27b-fp8_1x-rtx-pro-6000.md`): model loading
28.5 GiB, KV cache 54.2 GiB or 1,648,594 tokens, maximum concurrency 12.6x at 131,072 tokens, attention backend
FlashInfer, about 5 min from start to ready (loading from the old volume; local disk is quicker).

- If the smoke test fails on `/chat/completions`, try `--tool-call-parser qwen3_coder`.
  If only `/responses` fails, that's vLLM's Responses API: OpenCode works, Codex won't.
- Record load time, GPU KV cache size (4 agents x 131k needs
  ~524k tokens) and max concurrency at 131k (want >= 4x).
- During agent runs, `Prefix cache hit rate` in the log should climb on later turns.

## Serve: Qwen3.8-Flash-Next on 2 or 4 GPUs

The Flash-Next FP8 runs used the command below. Set `LOCAL_MODEL=qwen3.8-flash-next` in `.env` first. On a
4-GPU pod use `--tensor-parallel-size 4`.

```bash
vllm serve /root/models/qwen3.8-flash-next-fp8 \
  --served-model-name qwen3.8-flash-next \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 2 \
  --enable-expert-parallel \
  --max-model-len 131072 \
  --max-num-seqs 256 \
  --gpu-memory-utilization 0.90 \
  --attention-config.indexer_kv_dtype fp8 \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Why these settings, from the recipe:

- **Expert parallelism** (`--enable-expert-parallel`): with tensor parallelism, this is the recipe's recommended
  setup and faster than tensor parallelism alone. Pipeline parallelism is not supported.
- **The lookup table stays in host RAM:** vLLM offloads it by default (`--engram-config '{"cpu_offload":true}'`
  says so explicitly).
- **`--max-num-seqs 256`** avoids a cache capacity error at startup.
- **`--limit-mm-per-prompt`** turns off image and video input; the agents only send text.
- **131,072 context** is the same as the 27B, so the runs compare; the model's native maximum is 262,144.
- **MTP speculative decoding** is left off until the plain setup works. Then add
  `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`, which costs memory, and give those
  runs `BENCH_VARIANT=mtp3`.

Expect (estimates written before the first serve; no startup report from it was saved in `results/`):

- The startup log lists 2 (or 4) workers, one per GPU, and loads 186 GB from local disk.
- Weights take about 63 GiB per GPU at TP2 or 32 GiB at TP4 (134 GB without the lookup table, split evenly).
- At TP2 that leaves about 20 GiB per GPU for the KV cache, activations and CUDA graphs. Four agents at
  131k tokens need about 7 GB of KV cache in total (12 of 48 layers keep one, ~14 KB per token at FP8).
- Maximum concurrency at 131,072 tokens should be 4x or more.
- Several minutes from start to ready.

## Quantization comparison on 1 GPU

A model often has to fit hardware with less memory than the setup it was benchmarked on. Flash-Next at FP8, for
example, needs about 134 GB of GPU memory, just over what 4x RTX 5090 (128 GB) offers. Before choosing a
quantized model for that hardware, it is worth measuring what quantization costs on your own tasks. For
Flash-Next, 4-bit weights can be compared with its FP8 runs. A dense model of a size that fits without heroics
makes a useful reference at more than one precision: Qwen3.8-27B at BF16 against its FP8 runs.

All of these fit on one RTX PRO 6000. It is the same Blackwell generation (sm_120) as the RTX 5090, and with the
same weights and kernels, quality results carry over between the two. Speed and memory headroom do not (one GPU
here, several over PCIe there), so measure throughput on the hardware you will actually serve from.

Give the comparison runs the same `--served-model-name` (and `LOCAL_MODEL`) as the model's existing runs and
tell them apart with `BENCH_VARIANT`. The report then shows each variant in its own row next to the original:

| Runs | `LOCAL_MODEL` | `BENCH_VARIANT` |
| --- | --- | --- |
| Flash-Next NVFP4 | `qwen3.8-flash-next` | `nvfp4` |
| 27B BF16 | `qwen3.8-27b` | `bf16` |

For a clean comparison, change only the weights. Keep everything else as in the FP8 serve commands above: the
vLLM version (0.30.0), context length, parsers and KV cache type, with MTP off. Settings that only affect memory
use (`--gpu-memory-utilization`, `--max-num-seqs`) leave the answers unchanged.

### Qwen3.8-Flash-Next NVFP4 on 1 GPU

Served 2026-10-27 on 1x RTX PRO 6000 with vLLM 0.30.0, with the command below, for a full suite (28 runs).
Startup report: `results/2026-10-27_qwen3.8-flash-next-nvfp4_1x-rtx-pro-6000.md`.

`Inferact/Qwen3.8-Flash-Next-NVFP4` comes from the company behind vLLM and loads in vLLM. Only the routed experts
are quantized: NVFP4 weights and activations, ModelOpt format (68 GB). Everything else stays BF16 (attention,
shared experts, routers, embeddings, vision, MTP: about 12 GB), so the experts are the only part with lower
precision than the FP8 model. The n-gram lookup table (shard 1, 102 GB) is also BF16, twice the size of the FP8
model's, and it lives in host RAM. Loading it failed on a 1x RTX PRO 6000 pod limited to 125 GB
(`/sys/fs/cgroup/memory.max`) and worked on one with 250 GB, so check that limit before downloading.

`RadixArk/Qwen3.8-Flash-Next-NVFP4` is quantized the same way and keeps the table at FP8 (135 GB in total).
However, its model card lists only SGLang and describes it as a private candidate. The card also notes that
"long agentic generations tend to run longer than BF16". This applies to 4-bit Flash-Next generally, so compare
wall time and context per request against the FP8 runs, not just the scores.

```bash
vllm serve /root/models/qwen3.8-flash-next-nvfp4 \
  --served-model-name qwen3.8-flash-next \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --max-num-seqs 16 \
  --gpu-memory-utilization 0.95 \
  --attention-config.indexer_kv_dtype fp8 \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Changes from the FP8 command:

- **`--tensor-parallel-size 1`, no `--enable-expert-parallel`:** there is only one GPU. vLLM reads the
  quantization from the checkpoint's config, so `--quantization` isn't needed.
- **`--max-num-seqs 16` instead of 256:** each sequence slot reserves its linear attention (Gated DeltaNet)
  state, 36 layers of it. 256 slots fit across 2 or 4 GPUs, but on one they would take a large share of the
  memory left for the KV cache. A suite runs one agent at a time, so 16 slots leave plenty of headroom.
- **`--gpu-memory-utilization 0.95`:** the weights take most of the card.

Expect, from the first serve:

- NVFP4 MoE backend `FLASHINFER_CUTLASS`, and `Resolved Engram configuration: EngramConfig(cpu_offload=True, ...)`:
  the lookup table stays in host RAM without any flag.
- `Model loading took 73.53 GiB` (weights read in about 100 s from the container disk), then about 5.5 min of
  profiling, compiling and warm-up: about 8 min from start to ready.
- KV cache 14.39 GiB, `GPU KV cache size: 555,294 tokens`, `Maximum concurrency for 131,072 tokens per request:
  4.24x`. That is about 27 KB per token: the main KV cache stays BF16, as in the FP8 command, with the indexer key
  in FP8. Plenty for a suite (1x), just enough for a 4-agent load test.
- With one agent: about 75 generated tokens per second, prefix cache hit rate about 95%, KV cache use at most 18%.
- A few `Triton kernel JIT compilation during inference` warnings during the first runs: one-off latency spikes,
  harmless.

### Qwen3.8-27B BF16 on 1 GPU

The FP8 serve command with the BF16 weights and one addition, `--max-num-seqs 256`. `--kv-cache-dtype fp8` stays,
so the weights are the only difference from the FP8 runs:

```bash
vllm serve /root/models/qwen3.8-27b-bf16 \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --max-num-seqs 256 \
  --gpu-memory-utilization 0.92 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Without `--max-num-seqs`, startup fails with `max_num_seqs (1024) exceeds available Mamba cache blocks (661)`. The
27B is a hybrid model too: every sequence slot (vLLM's default is 1024) needs a block for its linear attention
state, and with the BF16 weights there is only room for 661. The FP8 serve had enough memory for the default.
The limit only caps how many requests run at once, so the answers don't change.

Expect, from the first serve (2026-10-27, vLLM 0.30.0, `results/2026-10-27_qwen3.8-27b-bf16_1x-rtx-pro-6000.md`):
`Model loading took 51.1 GiB` (weights read in 4 s, likely from the page cache right after the download), about
2 min from start to ready, KV cache 32.11 GiB, `GPU KV cache size: 975,758 tokens`, `Maximum concurrency for
131,072 tokens per request: 7.44x`. With one agent it generates about 24 tokens per
second, much slower than Flash-Next NVFP4 (about 75) on the same card: a dense model reads all 27B BF16 weights
for every token, Flash-Next only its few active experts. Plan for a full suite taking well over the FP8 runs'
6 hours.

## Serve: GLM-5.3-Flash on 4 GPUs

First served 2026-10-14 on 4x RTX PRO 6000 with vLLM 0.31.0. Set `LOCAL_MODEL=glm-5.3-flash` in `.env`
first (the smoke test fails with "model ... does not exist" when it doesn't match `--served-model-name`), and
install vLLM 0.31.0 (see Install vLLM).

```bash
export VLLM_ENGINE_READY_TIMEOUT_S=3600
vllm serve /root/models/glm-5.3-flash-fp8 \
  --served-model-name glm-5.3-flash \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 4 \
  --max-model-len 131072 \
  --max-num-seqs 16 \
  --kv-cache-memory=8000000000 \
  --no-enable-flashinfer-autotune \
  --reasoning-parser glm47 \
  --enable-auto-tool-choice --tool-call-parser glm47 \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

FP8 weights fit on 4 GPUs with a small KV cache. Per GPU, from the first serve (at
`--gpu-memory-utilization 0.95`, before `--kv-cache-memory`):

| Per GPU | GiB |
| --- | --- |
| Memory vLLM sees | 94.97 |
| Weights (`Model loading took`) | 76.91 |
| Weights plus memory outside PyTorch | 78.33 |
| Peak activations, measured while profiling | 6.32 |
| CUDA graphs | 0.09 |
| KV cache at `--gpu-memory-utilization 0.95` | 5.57 |
| KV cache that would fill the GPU completely (vLLM's own suggestion) | about 9.1 |

At 0.95 the KV cache held 679,191 tokens, `Maximum concurrency for 131,072 tokens per request: 5.18x`: about
35 KB per token across the four GPUs (8.8 KB on each), with a BF16 cache. `--kv-cache-memory` sets the KV
cache's size directly instead of as a share of the GPU: 8,000,000,000 bytes (7.45 GiB) gave 909,560 tokens,
6.94x, leaving about 1.7 GiB per GPU spare.

Why these settings:

- **No `--kv-cache-dtype fp8`:** it fails on this card with `pe_dim must be 64 for fp8_ds_mla`. vLLM's FP8
  layout for this kind of cache (MLA) expects a 64-number position part per token, which GLM-5.3-Flash
  doesn't have. The cache stays BF16, which is also slightly better quality.
- **`--kv-cache-memory=8000000000`** instead of `--gpu-memory-utilization`: the share left over after the
  weights and activations is small, so setting the KV cache's size directly gets a third more of it.
- **`--max-model-len 131072`** is the same context as the Qwen runs, so the runs compare.
- **`--max-num-seqs 16`** caps the batch, which shrinks the CUDA graphs and activation memory. Raise it if a
  load test runs more than 16 agents at once.
- **`--no-enable-flashinfer-autotune`**: FlashInfer's tuning step hung for 11 minutes on a 4x RTX PRO 6000 run.
- **`--reasoning-parser glm47`, `--tool-call-parser glm47`** are the vLLM recipe's. RedHatAI's NVFP4 model
  card uses `--reasoning-parser glm45`; try it if thinking text shows up in the answers.
- **`VLLM_ENGINE_READY_TIMEOUT_S=3600`**: profiling, compiling and warming up took about 4 minutes
  (`init engine ... took 247 s`), longer than some of vLLM's default waits.
- **MTP speculative decoding** is left off: its draft weights and cache would come out of the same small
  budget.

Expect, from the first serve: attention backend `FLASHINFER_MLA_SPARSE_SM120`, weights read in about 15 s from
the container disk (`Loading weights took`), and `GPU KV cache size: 909,560 tokens, Maximum concurrency for
131,072 tokens per request: 6.94x` with `--kv-cache-memory=8000000000` (5.18x at 0.95).

## Troubleshooting

- **GLM-5.3-Flash: out of memory at startup or under load:** lower `--kv-cache-memory` (6,000,000,000 is
  close to what 0.95 gave), or go back to `--gpu-memory-utilization 0.95`.
- **GLM-5.3-Flash: maximum concurrency below 4x:** lower `--max-model-len` to 100000 (shorter than the Qwen
  runs; give them `BENCH_VARIANT=ctx100k`), or serve the NVFP4 weights (`RedHatAI/GLM-5.3-Flash-NVFP4`, or
  `local-inference-lab/GLM-5.3-Flash-NVFP4` which a 4x RTX PRO 6000 run used) with `BENCH_VARIANT=nvfp4`.
- **GLM-5.3-Flash: `pe_dim must be 64 for fp8_ds_mla`:** remove `--kv-cache-dtype fp8` (see the serve
  section). Another startup error in sparse MLA can mean FlashInfer is older than 0.6.18; after that, try
  `--enforce-eager`.
- **Several GPUs: slow prompt processing, `NS` in `nvidia-smi topo -p2p r`:** the GPUs can't copy to each
  other directly, often because the host's IOMMU blocks it (fixed with the `iommu=pt` kernel parameter,
  which a rented pod can't change). It still works through host memory; a 4x RTX PRO 6000 GLM-5.3-Flash run
  read prompts 51 to 59% faster once direct copies worked. Redeploy if you want to compare speeds.
- **Flash-Next: `no kernel image is available for execution on the device`, or another startup error in a GPU
  kernel:** the risk of a card nobody validated it on (sm_120). Try in this order: `--moe-backend triton`,
  then `--enforce-eager` (no CUDA graphs: slower, but avoids graph capture), then a nightly vLLM build.
- **Flash-Next: out of memory while loading:** check the offload is on (add `--engram-config
  '{"cpu_offload":true}'`), lower `--max-model-len`, or use 4 GPUs.
- **Qwen hybrid models (27B, Flash-Next): `max_num_seqs (1024) exceeds available Mamba cache blocks (N)` at
  startup:** each sequence slot needs a block for its linear attention state, and vLLM's default of 1024 slots
  doesn't fit. Set `--max-num-seqs` below N (256 is the usual choice, 16 on a tight single GPU). This leaves the
  answers unchanged.
- **Flash-Next NVFP4: out of memory at startup, or maximum concurrency below 1x:** lowering
  `--max-num-batched-tokens` to 4096 shrinks activations without changing answers. Adding `--kv-cache-dtype fp8`
  frees more memory but changes answers slightly, so those runs need their own label
  (`BENCH_VARIANT=nvfp4-kvfp8`).
- **Flash-Next NVFP4: `Engine core initialization failed ... Failed core proc(s): {}` while loading weights,
  with no error from the engine itself:** the pod's RAM limit killed the engine. `memory.peak` equal to
  `memory.max` (both in `/sys/fs/cgroup/`) confirms it; `dmesg` usually shows nothing inside the container. The
  BF16 lookup table needs 102 GB plus headroom, more than the 125 GB a 1x RTX PRO 6000 pod gets.
- **NVFP4: `no kernel image is available`, or an error in an FP4 GEMM or MoE kernel:** the pinned vLLM may lack
  an sm_120 FP4 kernel for this model. A newer vLLM usually helps (with a `BENCH_VARIANT` naming it, since the
  engine version changed), and `--enforce-eager` is the fallback after that.
- **Several GPUs: startup hangs, every GPU at 100% use but low power (about 100 of 600 W):** the GPUs are
  waiting for each other, not computing. The hang can come right after `vLLM is using nccl` (NCCL connecting)
  or later, at the first forward passes (vLLM's own all-reduce, `CUSTOM` in the `all-reduce backends` log
  line, which writes straight into the other GPU's memory and ignores `NCCL_P2P_DISABLE`). Run the copy test
  in Check the pod: `WRONG DATA` means redeploy, or for quality runs only, the NCCL test and the workaround
  there. If every pair is `OK` but `topo -p2p r` says `NS`, start again with `NCCL_P2P_DISABLE=1` and
  `--disable-custom-all-reduce`.
- **GPU memory still in use after stopping vLLM:** Ctrl+C can leave the workers running. List them with
  `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` and `kill -9` each PID.
- **SSH drops immediately with "container ... is not running":** the container is
  crash-looping (usually the start command). Stop the pod and check its logs.
- **`RuntimeError: No supported CUDA architectures found for major versions [9, 10, 12]`:**
  FlashInfer is compiling with the template's CUDA 12.8 `nvcc`. Install the CUDA 13
  toolkit and set `CUDA_HOME`/`PATH` as above, in the same shell that starts vLLM.
- **`channel N: open failed: connect failed: Connection refused` in the tunnel:**
  something used the tunnel before vLLM was listening. Wait for startup; the tunnel is fine.
- **OpenCode hangs silently:** the tunnel is probably down; it retries without
  printing an error.

Sources: Qwen3.8-27B [model card](https://huggingface.co/Qwen/Qwen3.8-27B) and
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B); Qwen3.8-Flash-Next
[model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8) and
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) (as of 2026-10-13); GLM-5.3-Flash
[vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash),
[4x RTX PRO 6000 benchmark](https://github.com/commdata2338/glm-5.3-flash-4x-rtx-pro-6000-benchmark) and
[NVFP4 model card](https://huggingface.co/RedHatAI/GLM-5.3-Flash-NVFP4) (as of 2026-10-14); Flash-Next NVFP4
model cards and file listings from [Inferact](https://huggingface.co/Inferact/Qwen3.8-Flash-Next-NVFP4) and
[RadixArk](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4), and the 27B
[BF16 model](https://huggingface.co/Qwen/Qwen3.8-27B) (as of 2026-10-27).
