"""Prove a Redis snapshot restores stream IDs, pending ownership and cached results."""

import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import redis
import structlog

log = structlog.get_logger()


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, timeout=60).strip()


def connect(name: str) -> redis.Redis:
    port = int(docker("port", name, "6379/tcp").rsplit(":", 1)[1])
    client = redis.Redis(
        host="127.0.0.1",
        port=port,
        decode_responses=True,
        socket_timeout=3,
        socket_connect_timeout=3,
    )
    deadline = time.monotonic() + 15
    while True:
        try:
            client.ping()
            return client
        except redis.ConnectionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    names = [f"infer-backup-source-{suffix}", f"infer-backup-restored-{suffix}"]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="infer-backup-") as temp:
        root = Path(temp)
        source = target = None
        try:
            docker("run", "-d", "--name", names[0], "-p", "127.0.0.1::6379", "redis:7.4")
            source = connect(names[0])
            first = source.xadd("req:verification", {"request_id": "pending", "payload": "{}"})
            second = source.xadd("req:verification", {"request_id": "queued", "payload": "{}"})
            source.xgroup_create("req:verification", "workers", id="0")
            source.xreadgroup("workers", "killed-worker", {"req:verification": ">"}, count=1)
            source.set("result:finished", '{"status":"SUCCEEDED"}', ex=300)
            source.xadd("inbox:verification", {"request_id": "finished", "type": "final"})
            source.save()
            docker("cp", names[0] + ":/data/dump.rdb", str(root / "dump.rdb"))
            digest = hashlib.sha256((root / "dump.rdb").read_bytes()).hexdigest()
            docker("rm", "-f", names[0])
            # Copy the snapshot into an independent container, not the original data volume.
            docker("create", "--name", names[1], "-p", "127.0.0.1::6379", "redis:7.4")
            docker("cp", str(root / "dump.rdb"), names[1] + ":/data/dump.rdb")
            docker("start", names[1])
            target = connect(names[1])
            assert target.xlen("req:verification") == 2
            pending = target.xpending_range("req:verification", "workers", "-", "+", 10)
            assert len(pending) == 1 and pending[0]["message_id"] == first
            assert pending[0]["consumer"] == "killed-worker"
            assert (
                target.xreadgroup("workers", "new-worker", {"req:verification": ">"}, count=1)[0][
                    1
                ][0][0]
                == second
            )
            claimed = target.xautoclaim("req:verification", "workers", "new-worker", 0, "0-0")[1]
            assert first in [entry[0] for entry in claimed]
            assert target.get("result:finished") == '{"status":"SUCCEEDED"}'
            assert 0 < target.ttl("result:finished") <= 300
            assert target.xlen("inbox:verification") == 1
            report = {
                "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "redis_version": target.info("server")["redis_version"],
                "snapshot_sha256": digest,
                "restored_stream_entries": 2,
                "pending_ownership_restored": True,
                "pending_reclaim_succeeded": True,
                "undelivered_cursor_restored": True,
                "cached_result_and_ttl_restored": True,
                "inbox_restored": True,
                "elapsed_s": time.monotonic() - started,
                "scope": (
                    "Local RDB snapshot and independent container restore; "
                    "no HA, disk-loss RPO or continuous backup claim."
                ),
            }
            Path("docs/results/final-backup.json").write_text(json.dumps(report, indent=2) + "\n")
            log.info("backup_restore_verified", **report)
        finally:
            if source:
                source.close()
            if target:
                target.close()
            for name in names:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


if __name__ == "__main__":
    main()
