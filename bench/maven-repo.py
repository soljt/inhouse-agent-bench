#!/usr/bin/env python3
"""Throwaway Maven registry for bench tasks. Stands in for the GitLab package registry.

Serves GET from the run's upload store first, then from a read-only seed (what was already
published before the task). Accepts PUT into the upload store, except that a release
artifact which already exists is refused with 409, so versions are immutable like a real
registry. maven-metadata.xml and checksums of it may be rewritten. Every request is logged
in order, which is what the grader reads.

Paths look like GitLab's: /api/v4/projects/<id>/packages/maven/<group>/<artifact>/<version>/<file>.
Project ids map to separate stores; the seed belongs to project 11 (dev).

Usage: maven-repo.py --listen 172.17.0.1:8081 --seed DIR --store DIR --log FILE
"""
import argparse
import datetime
import http.server
import os
import re

PATH_RE = re.compile(r"^/api/v4/projects/(\d+)/packages/maven/(.+)$")
SEED_PROJECT = "11"


def is_mutable(rel):
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("maven-metadata.xml") or "-SNAPSHOT" in rel


class Handler(http.server.BaseHTTPRequestHandler):
    seed = store = logfile = None

    def log_line(self, status, extra=""):
        line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {self.command} {status} {self.path} {extra}".rstrip()
        with open(self.logfile, "a") as f:
            f.write(line + "\n")

    def resolve(self):
        m = PATH_RE.match(self.path.split("?", 1)[0])
        if not m or ".." in m.group(2).split("/"):
            return None, None, None
        project, rel = m.groups()
        uploaded = os.path.join(self.store, project, rel)
        seeded = os.path.join(self.seed, rel) if project == SEED_PROJECT else None
        return rel, uploaded, seeded

    def send(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        rel, uploaded, seeded = self.resolve()
        for path in (uploaded, seeded):
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    body = f.read()
                self.log_line(200)
                return self.send(200, body)
        self.log_line(404)
        self.send(404)

    do_HEAD = do_GET

    def do_PUT(self):
        rel, uploaded, seeded = self.resolve()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if rel is None:
            self.log_line(400)
            return self.send(400)
        exists = os.path.isfile(uploaded) or (seeded and os.path.isfile(seeded))
        if exists and not is_mutable(rel):
            self.log_line(409, "version already published")
            return self.send(409, b"Version already published; releases are immutable\n")
        os.makedirs(os.path.dirname(uploaded), exist_ok=True)
        with open(uploaded, "wb") as f:
            f.write(body)
        self.log_line(201)
        self.send(201)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="172.17.0.1:8081")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args()
    os.makedirs(a.store, exist_ok=True)
    Handler.seed, Handler.store, Handler.logfile = a.seed, a.store, a.log
    host, port = a.listen.rsplit(":", 1)
    http.server.ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
