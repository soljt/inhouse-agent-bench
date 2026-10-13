#!/usr/bin/env python3
"""Summarise a vLLM server log: startup memory numbers and the periodic throughput and KV cache stats.

Usage: bench/vllm_stats.py [log] [--since <time>] [--until <time>]
  e.g. ssh root@<pod-ip> -p <port> -i ~/.ssh/<key> cat /root/vllm.log | bench/vllm_stats.py --since 13:40

Reads the log from a file or stdin. --since and --until take the log's own clock (the pod's, check with
`date` there), as HH:MM[:SS] or MM-DD HH:MM[:SS]; a bare time applies to every day in the log.

vLLM prints a stats line every 10 seconds while it has work: prompt and generation throughput, running and
waiting requests, KV cache usage and prefix cache hit rate. They are grouped by the number of running
requests, which is the concurrency the server actually saw (agents between requests, running tools, don't
count), so a load test reads as throughput at 1, 2, 4, ... concurrent requests.
"""
import argparse
import re
import statistics
import sys
from collections import defaultdict

STAMP = re.compile(r'(\d\d-\d\d) (\d\d:\d\d:\d\d)')
FIELDS = {
    'prompt': re.compile(r'Avg prompt throughput: ([\d.]+) tokens/s'),
    'gen': re.compile(r'Avg generation throughput: ([\d.]+) tokens/s'),
    'running': re.compile(r'Running: (\d+) reqs'),
    'waiting': re.compile(r'Waiting: (\d+) reqs'),
    'kv': re.compile(r'GPU KV cache usage: ([\d.]+)%'),
    'prefix': re.compile(r'Prefix cache hit rate: ([\d.]+)%'),
}
STARTUP = {
    'Model loading (GiB per GPU)': re.compile(r'Model loading took ([\d.]+) GiB'),
    'Available KV cache memory (GiB per GPU)': re.compile(r'Available KV cache memory: ([\d.]+) GiB'),
    'GPU KV cache size (tokens)': re.compile(r'GPU KV cache size: ([\d,]+) tokens'),
    'Maximum concurrency': re.compile(r'Maximum concurrency for ([\d,]+ tokens per request: [\d.]+x)'),
}


def in_window(day, clock, since, until):
    """True when a log timestamp lies within --since/--until (each MM-DD HH:MM:SS or a bare time)."""
    for bound, inside in ((since, lambda a, b: a >= b), (until, lambda a, b: a <= b)):
        if not bound:
            continue
        value = f'{day} {clock}' if '-' in bound else clock
        if not inside(value, bound):
            return False
    return True


def parse(lines, since, until):
    """The stats samples within the window, and the startup values (the README's serve command starts a
    fresh log on every start, so there is one start per log)."""
    samples, startup = [], {}
    for line in lines:
        for name, rx in STARTUP.items():
            m = rx.search(line)
            if m:
                startup.setdefault(name, []).append(m.group(1))
        if 'Avg generation throughput' not in line:
            continue
        stamp = STAMP.search(line)
        if not stamp or not in_window(stamp.group(1), stamp.group(2), since, until):
            continue
        sample = {'time': f'{stamp.group(1)} {stamp.group(2)}'}
        for name, rx in FIELDS.items():
            m = rx.search(line)
            sample[name] = float(m.group(1)) if m else None
        samples.append(sample)
    return samples, startup


def mean_max(values):
    """'mean / max' of the values, or '-' when there are none."""
    values = [v for v in values if v is not None]
    if not values:
        return '-'
    return f'{statistics.mean(values):.1f} / {max(values):.1f}'


def report(samples, startup):
    """Prints the startup numbers, the window, a table per concurrency and the overall peaks."""
    print('## Startup')
    if not startup:
        print('(no startup lines in the log or window)')
    for name, values in startup.items():
        print(f'- {name}: {", ".join(dict.fromkeys(values))}')  # one value per GPU when they differ
    active = [s for s in samples if s['running']]
    print(f'\n## Load: {len(samples)} stats lines (10 s each), {len(active)} with requests running')
    if not samples:
        return
    print(f'{samples[0]["time"]} to {samples[-1]["time"]} (pod clock)\n')
    by_running = defaultdict(list)
    for s in active:
        by_running[int(s['running'])].append(s)
    print('| running reqs | samples | gen tok/s mean / max | gen tok/s per request | prompt tok/s mean | '
          'KV cache % mean / max | waiting max |')
    print('|---|---|---|---|---|---|---|')
    for n in sorted(by_running):
        group = by_running[n]
        gen = [s['gen'] for s in group if s['gen'] is not None]
        per_request = f'{statistics.mean(gen) / n:.1f}' if gen else '-'
        prompt = [s['prompt'] for s in group if s['prompt'] is not None]
        prompt_mean = f'{statistics.mean(prompt):.0f}' if prompt else '-'
        waiting = max(int(s['waiting'] or 0) for s in group)
        print(f'| {n} | {len(group)} | {mean_max(gen)} | {per_request} | {prompt_mean} | '
              f'{mean_max([s["kv"] for s in group])} | {waiting} |')
    queued = [s for s in samples if s['waiting']]
    print(f'\n- Peak KV cache usage: {max(s["kv"] or 0 for s in samples):.1f}%')
    print(f'- Lines with requests waiting (queued for KV cache or a slot): {len(queued)}')
    prefix = [s['prefix'] for s in samples if s['prefix'] is not None]
    if prefix:
        print(f'- Prefix cache hit rate at the end: {prefix[-1]:.1f}%')


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('log', nargs='?', default='-')
    p.add_argument('--since')
    p.add_argument('--until')
    a = p.parse_args(argv)
    stream = sys.stdin if a.log == '-' else open(a.log, errors='replace')
    samples, startup = parse(stream, a.since, a.until)
    report(samples, startup)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
