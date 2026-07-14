"""List newest stable Docker Hub tags for the images used in the compose stack.

Helper for version upgrades: prints the current tag next to the newest stable
tags so outdated images are easy to spot. Current versions are read from
cluster-setup/env/versions.env (the single source of truth) and, for the build
images not listed there, from kafka-producer-app/Dockerfile.kafka-producer-app.

Usage: python cluster-setup/scripts/check_image_tags.py
"""
import json
import re
import ssl
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_ENV = REPO_ROOT / "cluster-setup" / "env" / "versions.env"
PRODUCER_DOCKERFILE = REPO_ROOT / "kafka-producer-app" / "Dockerfile.kafka-producer-app"

# local TLS-intercepting proxy breaks verification; public read-only API
CTX = ssl._create_unverified_context()

# versions.env key -> (Docker Hub repo, regex for comparable stable tags,
# search mode). Mode "recent" pages through the most recently updated tags;
# "major" queries the name filter per major version (current..current+2) —
# needed where release tags are buried under thousands of CI tags (Superset).
# WEB_APP_VERSION is our own image, so it has no upstream to check.
ENV_REPOS = {
    "ZOOKEEPER_VERSION":       ("library/zookeeper",               r"^\d+\.\d+\.\d+$",  "recent"),
    "KAFKA_VERSION":           ("apache/kafka",                    r"^\d+\.\d+\.\d+$",  "recent"),
    "SCHEMA_REGISTRY_VERSION": ("confluentinc/cp-schema-registry", r"^\d+\.\d+\.\d+$",  "recent"),
    "PROMETHEUS_VERSION":      ("prom/prometheus",                 r"^v\d+\.\d+\.\d+$", "recent"),
    "GRAFANA_VERSION":         ("grafana/grafana",                 r"^\d+\.\d+\.\d+$",  "recent"),
    "PYTHON_VERSION":          ("library/python",                  r"^\d+\.\d+-slim$",  "recent"),
    "APACHE_PINOT_VERSION":    ("apachepinot/pinot",               r"^\d+\.\d+\.\d+$",  "recent"),
    "SUPERSET_VERSION":        ("apache/superset",                 r"^\d+\.\d+\.\d+$",  "major"),
}

# base images hardcoded in the kafka-producer-app Dockerfile FROM lines
DOCKERFILE_REPOS = {
    "maven":  ("library/maven",  r"^\d+\.\d+\.\d+-eclipse-temurin-\d+-alpine$", "recent"),
    "alpine": ("library/alpine", r"^\d+\.\d+$",                                 "recent"),
}

BAD = re.compile(r"snapshot|rc|beta|alpha|dev|preview", re.I)


def read_versions_env():
    versions = {}
    for line in VERSIONS_ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            versions[key] = value
    return versions


def read_dockerfile_tags():
    tags = {}
    for line in PRODUCER_DOCKERFILE.read_text().splitlines():
        m = re.match(r"FROM\s+([\w./-]+):(\S+)", line)
        if m and m.group(1) in DOCKERFILE_REPOS:
            tags[m.group(1)] = m.group(2)
    return tags


def ver_key(tag):
    return [int(x) for x in re.findall(r"\d+", tag)]


def check(repo, current, pattern, mode):
    pat = re.compile(pattern)
    base = f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100"
    if mode == "major":
        major = int(re.search(r"\d+", current).group())
        start_urls = [f"{base}&name={m}." for m in range(major, major + 3)]
    else:
        start_urls = [base]
    tags = set()
    for url in start_urls:
        for _ in range(8):  # up to 800 tags per query
            try:
                with urllib.request.urlopen(url, timeout=30, context=CTX) as r:
                    data = json.load(r)
            except Exception as e:
                print(f"{repo}: ERROR {e}")
                return
            for t in data.get("results", []):
                name = t["name"]
                if pat.match(name) and not BAD.search(name):
                    tags.add(name)
            url = data.get("next")
            if not url:
                break
    newest = sorted(tags, key=ver_key, reverse=True)[:6]
    print(f"{repo}  (current: {current})")
    print(f"  newest stable: {', '.join(newest) if newest else 'none found'}")


if __name__ == "__main__":
    versions = read_versions_env()
    for key, (repo, pattern, mode) in ENV_REPOS.items():
        current = versions.get(key)
        if current is None:
            print(f"{repo}: WARNING — {key} not found in {VERSIONS_ENV.name}")
            continue
        check(repo, current, pattern, mode)
    dockerfile_tags = read_dockerfile_tags()
    for image, (repo, pattern, mode) in DOCKERFILE_REPOS.items():
        check(repo, dockerfile_tags.get(image, "not found in Dockerfile"), pattern, mode)