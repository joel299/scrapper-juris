from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

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
                server.process_records("r1","res",source,source["source_url"],[record])
                server.process_records("r1","res",source,source["source_url"],[record])
                with server.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],1)
                    counts=dict(db.execute("SELECT status,COUNT(*) FROM run_items GROUP BY status").fetchall())
                    self.assertEqual(counts["persisted"],1)
                    self.assertEqual(counts["duplicate"],1)
            finally:
                server.DATA_DIR, server.DB_PATH = old_dir, old_path


if __name__ == "__main__":
    unittest.main()
