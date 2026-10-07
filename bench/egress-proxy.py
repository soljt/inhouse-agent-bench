#!/usr/bin/env python3
"""Allowlist HTTPS proxy: the only way out of a bench container.

Accepts CONNECT host:port only for allowlisted targets and logs every attempt, so the
run record shows what the agent tried to reach. Binds to the Docker bridge IP only.

Usage: egress-proxy.py --listen 172.17.0.1:3128 --allow api.openai.com:443 [--log file]
       egress-proxy.py --listen 172.17.0.1:8000 --forward 127.0.0.1:8000 [--log file]
The second form is a plain TCP forward, for an SSH tunnel that only listens on localhost.
"""
import argparse
import asyncio
import datetime


def log(path, msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_r, client_w, allow, logfile):
    peer = client_w.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        client_w.close()
        return
    request_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = request_line.split()
    method, target = (parts + ["", ""])[:2]
    if method != "CONNECT" or target not in allow:
        log(logfile, f"DENY  {peer[0]} {request_line}")
        client_w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await client_w.drain()
        client_w.close()
        return
    host, port = target.rsplit(":", 1)
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 15)
    except (OSError, asyncio.TimeoutError) as e:
        log(logfile, f"FAIL  {peer[0]} {target} {e}")
        client_w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await client_w.drain()
        client_w.close()
        return
    log(logfile, f"ALLOW {peer[0]} {target}")
    client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def forward(client_r, client_w, target, logfile):
    peer = client_w.get_extra_info("peername")
    host, port = target.rsplit(":", 1)
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 15)
    except (OSError, asyncio.TimeoutError) as e:
        log(logfile, f"FAIL  {peer[0]} {target} {e}")
        client_w.close()
        return
    log(logfile, f"FWD   {peer[0]} {target}")
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="172.17.0.1:3128")
    ap.add_argument("--allow", action="append", default=[], help="host:port, repeatable")
    ap.add_argument("--forward", help="host:port; forward every connection there instead of proxying")
    ap.add_argument("--log")
    a = ap.parse_args()
    host, port = a.listen.rsplit(":", 1)
    allow = set(a.allow)
    if a.forward:
        server = await asyncio.start_server(lambda r, w: forward(r, w, a.forward, a.log), host, int(port))
        log(a.log, f"listening on {a.listen}, forward to {a.forward}")
    else:
        server = await asyncio.start_server(lambda r, w: handle(r, w, allow, a.log), host, int(port))
        log(a.log, f"listening on {a.listen}, allow={sorted(allow)}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
