#!/usr/bin/env python3
"""The agents' network: no route out, no address of the host on it, and one gateway with a fixed set of ports.

Usage: bench/agentnet.py ensure <port> [<port> ...]

Agent containers join only the bench-agents network. It is --internal (no route anywhere) and the host has
no address on it (inhibit_ipv4), so sshd, rpcbind or anything else listening on the host is out of reach.
The bench-gateway container sits on this network and on Docker's default bridge. On bench-agents it is
called host.docker.internal, and it relays exactly the given ports (the model forwarder, the allowlist
proxy, the T2 registry) to the same ports on the real host, where run.sh's egress-proxy.py and
maven-repo.py listen. So harness configs keep using host.docker.internal:<port> unchanged.

ensure creates the network and the gateway if missing, and recreates the gateway when it lacks a port
(keeping the ports it had, for runs already going). It is safe to call from runs started in parallel.
"""
import os
import subprocess
import sys

NETWORK = 'bench-agents'
GATEWAY = 'bench-gateway'
ALIAS = 'host.docker.internal'
IMAGE = 'llm-bench'
PORTS_LABEL = 'bench.ports'
BENCH = os.path.dirname(os.path.abspath(__file__))


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True)


def ensure_network():
    if docker('network', 'inspect', NETWORK).returncode == 0:
        return
    res = docker('network', 'create', '--internal', '-o', 'com.docker.network.bridge.inhibit_ipv4=true', NETWORK)
    if res.returncode and 'already exists' not in res.stderr:
        sys.exit(f'agentnet: cannot create {NETWORK}: {res.stderr.strip()}')


def gateway_ports():
    """Ports of the running gateway, or None when there is none."""
    res = docker('inspect', '--format', '{{.State.Running}} {{index .Config.Labels "' + PORTS_LABEL + '"}}', GATEWAY)
    if res.returncode:
        return None
    running, _, ports = res.stdout.strip().partition(' ')
    return {int(p) for p in ports.split(',') if p} if running == 'true' else None


def start_gateway(ports):
    docker('rm', '-f', GATEWAY)
    proxy = os.path.join(BENCH, 'egress-proxy.py')
    relays = [arg for p in sorted(ports) for arg in ('--relay', str(p))]
    res = docker('run', '-d', '--name', GATEWAY, '--label', f'{PORTS_LABEL}={",".join(map(str, sorted(ports)))}',
                 '--add-host', f'{ALIAS}:host-gateway', '--user', f'{os.getuid()}:{os.getgid()}',
                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
                 '-v', f'{proxy}:/gateway/egress-proxy.py:ro', '--entrypoint', 'python3', IMAGE,
                 '/gateway/egress-proxy.py', '--relay-to', ALIAS, *relays)
    if res.returncode:
        if 'is already in use' in res.stderr:  # a parallel run started it first
            return
        sys.exit(f'agentnet: cannot start {GATEWAY}: {res.stderr.strip()}')
    res = docker('network', 'connect', '--alias', ALIAS, NETWORK, GATEWAY)
    if res.returncode:
        sys.exit(f'agentnet: cannot attach {GATEWAY} to {NETWORK}: {res.stderr.strip()}')


def ensure(ports):
    ensure_network()
    current = gateway_ports()
    if current is not None and set(ports) <= current:
        return
    start_gateway(set(ports) | (current or set()))


def main(args):
    if len(args) < 2 or args[0] != 'ensure':
        sys.exit(__doc__)
    ensure([int(p) for p in args[1:]])


if __name__ == '__main__':
    main(sys.argv[1:])
