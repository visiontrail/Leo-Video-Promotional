import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite

from backend import config, database
from backend.models import TaskConfig


class TaskDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_provider_table_gains_catalog_type_without_changing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy-providers.db"
            connection = await aiosqlite.connect(db_path)
            connection.row_factory = aiosqlite.Row
            await connection.execute(
                """CREATE TABLE providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    api_key TEXT,
                    model TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )"""
            )
            await connection.execute(
                """INSERT INTO providers
                   (name, endpoint, api_key, model, is_default, created_at)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (
                    "Internal gateway",
                    "http://oneapi.yhroot.com/v1/chat/completions",
                    "secret-key",
                    "yinhe-chat",
                    "2026-08-25T00:00:00+00:00",
                ),
            )
            await connection.commit()

            await database._migrate_providers(connection)
            row = (
                await connection.execute_fetchall(
                    "SELECT provider_type, endpoint, api_key, model FROM providers"
                )
            )[0]
            await connection.close()

        self.assertEqual(row["provider_type"], "yinhe")
        self.assertEqual(row["endpoint"], "http://oneapi.yhroot.com/v1/chat/completions")
        self.assertEqual(row["api_key"], "secret-key")
        self.assertEqual(row["model"], "yinhe-chat")

    async def test_provider_catalog_type_round_trips_through_database_response(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "providers.db"
            with (
                patch.object(config, "DB_PATH", db_path),
                patch.object(config, "AI_ENDPOINT", ""),
            ):
                await database.init_db()
                created = await database.create_provider(
                    provider_type="moonshot",
                    name="Kimi production",
                    endpoint="https://api.moonshot.cn/anthropic",
                    api_key="secret-key",
                    model="kimi-k3",
                    is_default=True,
                )
                listed = await database.list_providers()

        self.assertEqual(created.provider_type, "moonshot")
        self.assertEqual(created.api_key_masked, "secr...-key")
        self.assertEqual([provider.provider_type for provider in listed], ["moonshot"])

    async def test_generated_title_round_trips_through_task_response(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "tasks.db"
            with patch.object(config, "DB_PATH", db_path):
                await database.init_db()
                task = await database.create_task(
                    "youtube",
                    "https://example.com/video",
                    TaskConfig(),
                )
                self.assertIsNone(task.generated_title)

                await database.update_task(task.id, generated_title="A New Publication Title")
                refreshed = await database.get_task(task.id)

            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed.generated_title, "A New Publication Title")

    async def test_legacy_task_table_migration_adds_generated_title(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            connection = await aiosqlite.connect(db_path)
            connection.row_factory = aiosqlite.Row
            await connection.execute(
                """CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    scheduled_at TEXT,
                    thumbnail_path TEXT
                )"""
            )
            await connection.commit()

            await database._migrate_tasks(connection)
            columns = {
                row["name"]
                for row in await connection.execute_fetchall("PRAGMA table_info(tasks)")
            }
            await connection.close()

            self.assertIn("generated_title", columns)

    async def test_reset_orphaned_tasks_closes_connection_after_lock_error(self):
        connection = AsyncMock()
        connection.execute.side_effect = sqlite3.OperationalError("database is locked")

        with patch.object(database, "get_db", AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                await database.reset_orphaned_tasks()

        connection.rollback.assert_awaited_once()
        connection.close.assert_awaited_once()

    async def test_claim_account_run_closes_transaction_when_cancelled(self):
        connection = AsyncMock()
        connection.execute.side_effect = asyncio.CancelledError

        with patch.object(database, "get_db", AsyncMock(return_value=connection)):
            with self.assertRaises(asyncio.CancelledError):
                await database.claim_next_account_run()

        connection.rollback.assert_awaited_once()
        connection.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
