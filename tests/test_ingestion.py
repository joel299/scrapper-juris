from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from app import server


class IngestionTests(unittest.TestCase):
    def test_rejects_non_official_hosts_and_plain_http(self):
        for url in (
            "http://dadosabertos.web.stj.jus.br/dataset/example",
            "https://example.com/dataset/example",
            "https://dadosabertos.web.stj.jus.br.evil.example/dataset/x",
            "https://dadosabertos.web.stj.jus.br:8443/dataset/x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.safe_public_url(url)

    def test_redirect_to_untrusted_host_is_rejected(self):
        handler = server.OfficialRedirectHandler()
        request = server.Request("https://dadosabertos.web.stj.jus.br/dataset/x")
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://127.0.0.1/admin")

    def test_html_200_is_not_json(self):
        with self.assertRaisesRegex(ValueError, "não contém JSON válido"):
            server.parse_payload(b"<!doctype html><html><body>erro</body></html>")

    def test_json_keeps_unknown_source_fields(self):
        records = server.records_from_json(b'{"acordaos":[{"id":"A1","campo_novo":"valor"}]}')
        self.assertEqual(records, [{"id": "A1", "campo_novo": "valor"}])
        self.assertEqual(server.record_id(records[0]), "A1")

    def test_valid_zip_and_rejects_traversal(self):
        good = io.BytesIO()
        with zipfile.ZipFile(good, "w") as zf:
            zf.writestr("records.json", '[{"id":"ok"}]')
        self.assertEqual(server.parse_payload(good.getvalue()), [{"id": "ok"}])
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("../outside.json", '[{"id":"bad"}]')
        with self.assertRaisesRegex(ValueError, "caminho inseguro"):
            server.parse_payload(bad.getvalue())

    def test_persists_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_path = server.DATA_DIR, server.DB_PATH
            server.DATA_DIR = Path(tmp)
            server.DB_PATH = Path(tmp) / "test.sqlite3"
            try:
                server.init_db()
                source = {"id":"s1","source_url":"https://dadosabertos.web.stj.jus.br/dataset/x","tribunal":"STJ","dataset":"x"}
                with server.connect() as db:
                    db.execute("INSERT INTO sources(id,source_url,tribunal,dataset,created_at) VALUES(?,?,?,?,?)",("s1",source["source_url"],"STJ","x",server.now()))
                    db.execute("INSERT INTO runs(id,status,started_at,source_count) VALUES('r1','persisting',?,1)",(server.now(),))
                    db.execute("INSERT INTO run_resources(id,run_id,source_id,source_url,resource_url,tribunal,dataset,status) VALUES('res','r1','s1',?,?, 'STJ','x','downloaded')",(source["source_url"],source["source_url"]))
                record={"id":"case-1","ementa":"texto"}
                with patch.object(server, "DATABASE_URL", ""):
                    server.process_records("r1","res",source,source["source_url"],[record])
                    server.process_records("r1","res",source,source["source_url"],[record])
                with server.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],1)
                    counts=dict(db.execute("SELECT status,COUNT(*) FROM run_items GROUP BY status").fetchall())
                    self.assertEqual(counts["persisted"],1)
                    self.assertEqual(counts["duplicate"],1)
            finally:
                server.DATA_DIR, server.DB_PATH = old_dir, old_path

    def test_postgres_date_formats(self):
        self.assertEqual(str(server.parse_stj_date("20201125")), "2020-11-25")
        self.assertEqual(str(server.parse_stj_date("DJE DATA:18/12/2020")), "2020-12-18")
        self.assertIsNone(server.parse_stj_date("unknown"))

    def test_postgres_document_mapping_preserves_raw_payload_and_hash(self):
        import hashlib
        record = {"id":"case-1","numeroProcesso":"123","dataDecisao":"20201125","ementa":"Ementa","decisao":"Acordam"}
        mapped = server.map_postgres_document(
            {"tribunal":"STJ","source_url":"https://dadosabertos.web.stj.jus.br/dataset/x"},
            "https://dadosabertos.web.stj.jus.br/dataset/x", record, "2026-11-05T00:00:00+00:00",
        )
        self.assertEqual(mapped[0], "case-1")
        self.assertEqual(mapped[1], "STJ")
        self.assertEqual(mapped[6].isoformat(), "2020-11-25")
        self.assertEqual(mapped[13], hashlib.sha256(mapped[14].encode()).hexdigest())
        self.assertEqual(json.loads(mapped[14]), record)

    def test_process_records_uses_postgres_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_path = server.DATA_DIR, server.DB_PATH
            server.DATA_DIR = Path(tmp)
            server.DB_PATH = Path(tmp) / "test.sqlite3"
            try:
                server.init_db()
                source = {"id":"s1","source_url":"https://dadosabertos.web.stj.jus.br/dataset/x","tribunal":"STJ","dataset":"x"}
                with server.connect() as db:
                    db.execute("INSERT INTO sources(id,source_url,tribunal,dataset,created_at) VALUES(?,?,?,?,?)",("s1",source["source_url"],"STJ","x",server.now()))
                    db.execute("INSERT INTO runs(id,status,started_at,source_count) VALUES('r1','persisting',?,1)",(server.now(),))
                    db.execute("INSERT INTO run_resources(id,run_id,source_id,source_url,resource_url,tribunal,dataset,status) VALUES('res','r1','s1',?,?, 'STJ','x','downloaded')",(source["source_url"],source["source_url"]))
                record={"id":"case-1","ementa":"texto"}
                with patch.object(server, "DATABASE_URL", "postgresql://configured"), patch.object(server, "persist_postgres_records", return_value=(1,0,0)) as persist:
                    server.process_records("r1","res",source,source["source_url"],[record])
                persist.assert_called_once_with(source, source["source_url"], [record])
                with server.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],0)
                    self.assertEqual(db.execute("SELECT inserted_count FROM runs WHERE id='r1'").fetchone()[0],1)
                    self.assertEqual(db.execute("SELECT status FROM run_items WHERE run_id='r1'").fetchone()[0],"postgres_persisted")
            finally:
                server.DATA_DIR, server.DB_PATH = old_dir, old_path


    def test_load_local_env_preserves_existing_values_and_unquotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TEST_SCRAPPER_JURIS=from-file\nTEST_SCRAPPER_QUOTED='quoted value'\n", encoding="utf-8")
            key1, key2 = "TEST_SCRAPPER_JURIS", "TEST_SCRAPPER_QUOTED"
            with patch.object(server, "ROOT", Path(tmp)), patch.dict("os.environ", {key1: "from-process"}, clear=False):
                import os
                os.environ.pop(key2, None)
                server.load_local_env()
                self.assertEqual(os.environ[key1], "from-process")
                self.assertEqual(os.environ[key2], "quoted value")
                os.environ.pop(key2, None)

    def test_postgres_driver_errors_do_not_leak_uri(self):
        import sys
        from types import SimpleNamespace
        secret_uri = "postgresql://user:super-secret@db.example:5432/app"
        def fail_connect(*args, **kwargs):
            raise RuntimeError(f"connection failed for {secret_uri}")
        fake_driver = SimpleNamespace(connect=fail_connect)
        with patch.dict(sys.modules, {"psycopg": fake_driver}), patch.object(server, "DATABASE_URL", secret_uri):
            with self.assertRaises(RuntimeError) as caught:
                server.postgres_connection()
        self.assertNotIn("super-secret", str(caught.exception))
        self.assertNotIn("db.example", str(caught.exception))

    def test_schema_inspection_returns_only_column_metadata(self):
        class FakeDb:
            def execute(self, query, params):
                self.query, self.params = query, params
                class Result:
                    def fetchall(self):
                        return [
                            {"table_name":"jurisprudence_documents","column_name":"id","data_type":"uuid","udt_name":"uuid","is_nullable":"NO","is_identity":"NO"},
                            {"table_name":"jurisprudence_documents","column_name":"source_url","data_type":"text","udt_name":"text","is_nullable":"YES","is_identity":"NO"},
                        ]
                return Result()
        db = FakeDb()
        with patch.object(server, "postgres_connection", return_value=nullcontext(db)):
            result = server.postgres_schema("public")
        self.assertEqual(result["schema"], "public")
        self.assertEqual(result["tables"][0]["name"], "jurisprudence_documents")
        self.assertEqual([c["name"] for c in result["tables"][0]["columns"]], ["id", "source_url"])
        self.assertNotIn("rows", result)
        self.assertEqual(db.params, ("public",))
        self.assertIn("information_schema.columns", db.query)

    def test_schema_name_is_validated_before_connecting(self):
        with patch.object(server, "postgres_connection") as connection:
            with self.assertRaises(ValueError):
                server.postgres_schema("public; DROP SCHEMA public")
            connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
