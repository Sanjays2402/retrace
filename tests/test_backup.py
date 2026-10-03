from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from retrace import Engine, Store, Task, Workflow


async def prepare(ctx):
    return {"prepared": ctx.input}


async def publish(ctx):
    return {"input": ctx.dependencies["prepare"], "approval": ctx.signal}


class BackupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "runs.db")
        self.destination = Path(self.directory.name, "snapshot.db")
        self.store = Store(self.path)
        self.workflow = Workflow(
            "backup",
            (
                Task("prepare", prepare),
                Task("publish", publish, needs=("prepare",), wait_for="approval"),
            ),
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def assert_no_temporary_files(self):
        self.assertEqual(list(Path(self.directory.name).glob(".retrace-backup-*")), [])

    async def test_restore_resumes_waiting_run_without_repeating_checkpoint(self):
        run_id = self.store.create(self.workflow, 42, key="release-42")
        await Engine(self.store).resume(self.workflow, run_id)
        self.store.configure_queue(self.workflow, max_active=2, max_queued=10)
        result = self.store.backup(self.destination)
        self.assertEqual(result.runs, 1)
        self.assertEqual(result.schema_version, 4)
        self.assertEqual(result.size_bytes, self.destination.stat().st_size)
        self.assertEqual(result.path, str(self.destination))
        with Store(self.destination) as restored:
            self.assertEqual(restored.run(run_id), self.store.run(run_id))
            self.assertEqual(restored.tasks(run_id), self.store.tasks(run_id))
            self.assertEqual(restored.history(run_id), self.store.history(run_id))
            self.assertEqual(restored.events(run_id), self.store.events(run_id))
            self.assertEqual(restored.create(self.workflow, 42, key="release-42"), run_id)
            self.assertEqual(restored.queue_stats()[0]["max_active"], 2)
            restored.signal(run_id, "approval", {"approved": True})
            completed = await Engine(restored).resume(self.workflow, run_id)
            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(restored.tasks(run_id)["prepare"]["attempts"], 1)
            self.assertEqual(completed.outputs["publish"]["approval"], {"approved": True})
        self.assertEqual(self.store.run(run_id)["status"], "waiting")
        self.assert_no_temporary_files()

    async def test_readonly_source_includes_wal_and_snapshot_is_standalone(self):
        run_id = self.store.create(self.workflow, 42)
        self.store.signal(run_id, "approval", "yes")
        await Engine(self.store).resume(self.workflow, run_id)
        self.assertGreater(Path(str(self.path) + "-wal").stat().st_size, 0)
        with Store(self.path, readonly=True) as source:
            source.backup(self.destination)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.destination) + suffix).exists())
        with Store(self.destination, readonly=True) as snapshot:
            self.assertEqual(snapshot.signals(run_id), self.store.signals(run_id))
            self.assertEqual(snapshot.run(run_id)["status"], "succeeded")
            self.assertEqual(snapshot.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        if os.name == "posix":
            self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_existing_paths_and_invalid_options_are_rejected(self):
        self.destination.write_bytes(b"keep this backup")
        with self.assertRaises(FileExistsError):
            self.store.backup(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep this backup")
        with self.assertRaises(FileExistsError):
            self.store.backup(self.path)
        for timeout in (True, 0, -1, float("inf"), float("nan"), "30"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.store.backup(self.destination, timeout=timeout)
        with self.store.transaction(), self.assertRaisesRegex(ValueError, "transaction"):
            self.store.backup(self.destination)
        with self.assertRaises(FileNotFoundError):
            self.store.backup(Path(self.directory.name, "missing", "backup.db"))
        self.assert_no_temporary_files()

    def test_copy_timeout_removes_partial_files(self):
        with (
            patch("retrace.store.time.monotonic", side_effect=(0, 2)),
            self.assertRaises(TimeoutError),
        ):
            self.store.backup(self.destination, timeout=1)
        self.assertFalse(self.destination.exists())
        self.assert_no_temporary_files()

    def test_source_and_destination_sidecar_paths_are_protected(self):
        with self.assertRaises(ValueError):
            self.store.backup(str(self.path) + "-journal")
        sidecar = Path(str(self.destination) + "-wal")
        sidecar.write_bytes(b"existing WAL")
        with self.assertRaises(FileExistsError):
            self.store.backup(self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(sidecar.read_bytes(), b"existing WAL")
        self.assert_no_temporary_files()

    def test_in_memory_store_can_be_saved_to_disk(self):
        with Store(":memory:") as memory:
            run_id = memory.create(self.workflow, 42)
            result = memory.backup(self.destination)
            self.assertEqual(result.runs, 1)
        with Store(self.destination, readonly=True) as restored:
            self.assertEqual(restored.run(run_id)["input"], 42)
            self.assertEqual(len(restored.tasks(run_id)), 2)

    def test_publication_race_preserves_the_other_process_file(self):
        def competing_publish(_source, destination):
            Path(destination).write_bytes(b"another backup")
            raise FileExistsError("another process won")

        with (
            patch("retrace.store.os.link", side_effect=competing_publish),
            self.assertRaises(FileExistsError),
        ):
            self.store.backup(self.destination)
        self.assertEqual(self.destination.read_bytes(), b"another backup")
        self.assert_no_temporary_files()

    def test_foreign_key_corruption_prevents_publication(self):
        self.store.db.execute("PRAGMA foreign_keys=OFF")
        self.store.db.execute("INSERT INTO tasks(run_id,name) VALUES('missing','orphan')")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "foreign key"):
            self.store.backup(self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_no_temporary_files()

    async def test_concurrent_producer_preserves_atomic_run_records_in_snapshot(self):
        ready = threading.Event()

        def produce():
            with Store(self.path) as producer:
                for index in range(30):
                    producer.create(self.workflow, {"index": index, "data": "x" * 10000})
                    ready.set()
                    time.sleep(0.002)

        writer = asyncio.create_task(asyncio.to_thread(produce))
        try:
            self.assertTrue(await asyncio.to_thread(ready.wait, 5))
            with Store(self.path, readonly=True) as source:
                result = source.backup(self.destination)
            with Store(self.destination, readonly=True) as snapshot:
                self.assertGreaterEqual(result.runs, 1)
                self.assertLessEqual(result.runs, 30)
                self.assertEqual(len(snapshot.runs()), result.runs)
                for run in snapshot.runs():
                    self.assertEqual(len(snapshot.tasks(run["id"])), 2)
                    self.assertEqual(len(snapshot.events(run["id"])), 1)
                self.assertEqual(snapshot.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            await writer
