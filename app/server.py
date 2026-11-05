from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SCRAPER_DATA_DIR", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "scraper.sqlite3"
STATIC_DIR = ROOT / "static"
MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 5000
USER_AGENT = "AurumLexJurisprudenceImporter/0.1 (respectful public-data client)"
ALLOWED_HOSTS = {"dadosabertos.web.stj.jus.br", "www.stj.jus.br", "stj.jus.br"}
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, source_url TEXT NOT NULL UNIQUE,
          tribunal TEXT NOT NULL DEFAULT 'STJ', dataset TEXT,
          created_at TEXT NOT NULL, last_status TEXT, last_captured_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT, source_count INTEGER NOT NULL DEFAULT 0,
          discovered_count INTEGER NOT NULL DEFAULT 0, downloaded_count INTEGER NOT NULL DEFAULT 0,
          processed_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
          updated_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0,
          error_count INTEGER NOT NULL DEFAULT 0, safe_error_summary TEXT
        );
        CREATE TABLE IF NOT EXISTS run_resources (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          source_id TEXT NOT NULL, source_url TEXT NOT NULL, resource_url TEXT,
          tribunal TEXT NOT NULL DEFAULT 'STJ', dataset TEXT, status TEXT NOT NULL,
          content_type TEXT, content_length INTEGER, sha256 TEXT,
          fetched_at TEXT, safe_error_summary TEXT
        );
        CREATE TABLE IF NOT EXISTS documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT, tribunal TEXT NOT NULL,
          dataset TEXT, source_record_id TEXT NOT NULL, source_url TEXT NOT NULL,
          resource_url TEXT NOT NULL, license TEXT, source_updated_at TEXT,
          captured_at TEXT NOT NULL, content_hash TEXT NOT NULL,
          raw_json TEXT NOT NULL,
          UNIQUE(tribunal, dataset, source_record_id)
        );
        CREATE TABLE IF NOT EXISTS run_items (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
          resource_id TEXT REFERENCES run_resources(id), source_record_id TEXT,
          status TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
          content_hash TEXT, safe_error_summary TEXT, processed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_run_status ON run_items(run_id,status);
        CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
        """)


def safe_public_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443):
        raise ValueError("Somente URLs HTTPS oficiais do domínio STJ são permitidas.")
    if parsed.username or parsed.password:
        raise ValueError("URL com credenciais embutidas não permitida.")
    return raw


class ResourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href and re.search(r"\.(json|zip)(?:$|[?#])", href, re.I):
                self.links.append(href)


class OfficialRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate each hop before urllib follows it; never redirect to arbitrary hosts.
        safe_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = build_opener(OfficialRedirectHandler)


def fetch(url: str, limit: int = MAX_DOWNLOAD) -> tuple[bytes, str, str]:
    safe_public_url(url)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json,application/zip,*/*;q=0.5"})
    try:
        with OPENER.open(req, timeout=20) as response:
            final_url = safe_public_url(response.geturl())
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            if status == 403:
                raise PermissionError("Fonte bloqueou a requisição (403); captura interrompida para esta URL.")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Recurso excede o limite de tamanho configurado.")
            return data, content_type, final_url
    except HTTPError as exc:
        if exc.code == 403:
            raise PermissionError("Fonte bloqueou a requisição (403); captura interrompida para esta URL.") from None
        raise ValueError(f"Fonte respondeu HTTP {exc.code}.") from None
    except (URLError, TimeoutError) as exc:
        raise ValueError(f"Falha de rede ao consultar a fonte: {type(exc).__name__}.") from None


def discover_resources(source_url: str) -> list[str]:
    data, content_type, final_url = fetch(source_url, limit=4 * 1024 * 1024)
    if data.startswith(b"PK\x03\x04") or "json" in content_type.lower():
        return [final_url]
    if "html" not in content_type.lower() and not data.lstrip().startswith((b"<!doctype html", b"<html", b"<")):
        raise ValueError("Resposta não é uma página HTML, JSON ou ZIP reconhecível.")
    parser = ResourceLinks()
    parser.feed(data.decode("utf-8", errors="replace"))
    results = []
    for href in parser.links:
        resolved = safe_public_url(urljoin(final_url, href))
        if resolved not in results:
            results.append(resolved)
    if not results:
        raise ValueError("Nenhum link JSON/ZIP foi encontrado na página oficial.")
    return results


def records_from_json(raw: bytes) -> list[dict]:
    try:
        obj = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Conteúdo JSON inválido.") from None
    if isinstance(obj, dict):
        for key in ("data", "resultados", "acordaos", "items", "records", "results"):
            value = obj.get(key)
            if isinstance(value, list):
                obj = value
                break
        else:
            obj = [obj]
    if not isinstance(obj, list) or not all(isinstance(x, dict) for x in obj):
        raise ValueError("Estrutura JSON não contém uma lista de registros objeto.")
    return obj


def parse_payload(payload: bytes) -> list[dict]:
    if payload.startswith(b"PK\x03\x04"):
        import io
        records: list[dict] = []
        total = 0
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = [i for i in archive.infolist() if not i.is_dir()]
            if len(files) > MAX_FILES:
                raise ValueError("ZIP contém quantidade excessiva de arquivos.")
            for info in files:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("ZIP contém caminho inseguro.")
                if info.file_size > MAX_EXPANDED or (info.compress_size and info.file_size / info.compress_size > 200):
                    raise ValueError("ZIP excede limite de expansão segura.")
                if not name.lower().endswith(".json"):
                    continue
                total += info.file_size
                if total > MAX_EXPANDED:
                    raise ValueError("ZIP excede limite total descompactado.")
                records.extend(records_from_json(archive.read(info)))
        return records
    if not payload.lstrip().startswith((b"{", b"[")):
        raise ValueError("Resposta HTTP não contém JSON válido nem assinatura ZIP.")
    return records_from_json(payload)


def pick(record: dict, names: tuple[str, ...]):
    # Match exact source keys only; do not infer semantic values from arbitrary fields.
    for name in names:
        if name in record and record[name] not in (None, ""):
            return str(record[name])
    return None


def record_id(record: dict) -> str:
    value = pick(record, ("id", "ID", "Id", "identificador", "numeroRegistro", "NUM_REGISTRO"))
    if value:
        return value
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def create_run(source_ids: list[str]) -> str:
    run_id = str(uuid.uuid4())
    with connect() as db:
        marks = ",".join("?" for _ in source_ids)
        sources = db.execute(f"SELECT * FROM sources WHERE id IN ({marks})", source_ids).fetchall()
        if len(sources) != len(set(source_ids)):
            raise ValueError("Uma ou mais fontes selecionadas não existem.")
        db.execute("INSERT INTO runs(id,status,started_at,source_count) VALUES(?,?,?,?)", (run_id, "queued", now(), len(sources)))
    threading.Thread(target=run_worker, args=(run_id, [dict(s) for s in sources]), daemon=True).start()
    return run_id


def run_worker(run_id: str, sources: list[dict]) -> None:
    with connect() as db:
        db.execute("UPDATE runs SET status='discovering' WHERE id=?", (run_id,))
    for source in sources:
        try:
            urls = discover_resources(source["source_url"])
        except Exception as exc:
            add_resource_error(run_id, source, str(exc))
            continue
        for resource_url in urls:
            resource_id = str(uuid.uuid4())
            with connect() as db:
                db.execute("INSERT INTO run_resources(id,run_id,source_id,source_url,resource_url,tribunal,dataset,status) VALUES(?,?,?,?,?,?,?,'discovered')",
                           (resource_id, run_id, source["id"], source["source_url"], resource_url, source["tribunal"], source["dataset"]))
                db.execute("UPDATE runs SET discovered_count=discovered_count+1,status='downloading' WHERE id=?", (run_id,))
            try:
                payload, content_type, final_url = fetch(resource_url)
                if not (payload.startswith(b"PK\x03\x04") or payload.lstrip().startswith((b"{", b"["))):
                    raise ValueError("Resposta HTTP 200 não é JSON/ZIP válido (HTML ou conteúdo inesperado).")
                digest = hashlib.sha256(payload).hexdigest()
                with connect() as db:
                    db.execute("UPDATE run_resources SET status='downloaded',content_type=?,content_length=?,sha256=?,fetched_at=? WHERE id=?",
                               (content_type[:100], len(payload), digest, now(), resource_id))
                    db.execute("UPDATE runs SET downloaded_count=downloaded_count+1,status='parsing' WHERE id=?", (run_id,))
                records = parse_payload(payload)
                with connect() as db:
                    db.execute("UPDATE runs SET processed_count=processed_count+? ,status='persisting' WHERE id=?", (len(records), run_id))
                process_records(run_id, resource_id, source, final_url, records)
                with connect() as db:
                    db.execute("UPDATE run_resources SET status='completed' WHERE id=?", (resource_id,))
            except Exception as exc:
                add_resource_error(run_id, source, str(exc), resource_id)
    with connect() as db:
        row = db.execute("SELECT error_count FROM runs WHERE id=?", (run_id,)).fetchone()
        status = "completed_with_errors" if row["error_count"] else "completed"
        db.execute("UPDATE runs SET status=?,finished_at=? WHERE id=?", (status, now(), run_id))
        db.execute("UPDATE sources SET last_status=?,last_captured_at=? WHERE id IN (SELECT source_id FROM run_resources WHERE run_id=?)",
                   (status, now(), run_id))


def add_resource_error(run_id: str, source: dict, message: str, resource_id: str | None = None) -> None:
    # Summaries deliberately omit exception strings that may echo remote content/secrets.
    safe = message[:400]
    state = "blocked" if "(403)" in message else "failed"
    with connect() as db:
        if resource_id:
            db.execute("UPDATE run_resources SET status=?,safe_error_summary=? WHERE id=?", (state, safe, resource_id))
        else:
            resource_id = str(uuid.uuid4())
            db.execute("INSERT INTO run_resources(id,run_id,source_id,source_url,tribunal,dataset,status,safe_error_summary) VALUES(?,?,?,?,?,?,?,?)",
                       (resource_id, run_id, source["id"], source["source_url"], source["tribunal"], source["dataset"], state, safe))
        db.execute("INSERT INTO run_items(id,run_id,resource_id,status,safe_error_summary,processed_at) VALUES(?,?,?,?,?,?)",
                   (str(uuid.uuid4()), run_id, resource_id, state, safe, now()))
        db.execute("UPDATE runs SET error_count=error_count+1,safe_error_summary=COALESCE(safe_error_summary,?) WHERE id=?", (safe, run_id))


def process_records(run_id: str, resource_id: str, source: dict, resource_url: str, records: list[dict]) -> None:
    for record in records:
        rid = record_id(record)
        raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        item_id = str(uuid.uuid4())
        captured = now()
        try:
            with connect() as db:
                existing = db.execute("SELECT id,content_hash FROM documents WHERE tribunal=? AND dataset IS ? AND source_record_id=?",
                                      (source["tribunal"], source["dataset"], rid)).fetchone()
                if existing and existing["content_hash"] == digest:
                    db.execute("INSERT INTO run_items(id,run_id,resource_id,source_record_id,status,document_id,content_hash,processed_at) VALUES(?,?,?,?,'duplicate',?,?,?)",
                               (item_id, run_id, resource_id, rid, existing["id"], digest, captured))
                    db.execute("UPDATE runs SET duplicate_count=duplicate_count+1 WHERE id=?", (run_id,))
                else:
                    vals=(source["tribunal"],source["dataset"],rid,source["source_url"],resource_url,pick(record,("licenca","license")),pick(record,("dataAtualizacao","updated_at")),captured,digest,raw)
                    db.execute("""INSERT INTO documents(tribunal,dataset,source_record_id,source_url,resource_url,license,source_updated_at,captured_at,content_hash,raw_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tribunal,dataset,source_record_id) DO UPDATE SET
                      source_url=excluded.source_url,resource_url=excluded.resource_url,license=excluded.license,
                      source_updated_at=excluded.source_updated_at,captured_at=excluded.captured_at,content_hash=excluded.content_hash,raw_json=excluded.raw_json""", vals)
                    doc=db.execute("SELECT id FROM documents WHERE tribunal=? AND dataset IS ? AND source_record_id=?",(source["tribunal"],source["dataset"],rid)).fetchone()
                    status = "updated" if existing else "persisted"
                    db.execute("INSERT INTO run_items(id,run_id,resource_id,source_record_id,status,document_id,content_hash,processed_at) VALUES(?,?,?,?,?,?,?,?)",
                               (item_id,run_id,resource_id,rid,status,doc["id"],digest,captured))
                    db.execute("UPDATE runs SET inserted_count=inserted_count+?,updated_count=updated_count+? WHERE id=?", (int(not existing),int(bool(existing)),run_id))
        except sqlite3.Error:
            with connect() as db:
                db.execute("INSERT INTO run_items(id,run_id,resource_id,source_record_id,status,safe_error_summary,processed_at) VALUES(?,?,?,?,'failed','Falha ao persistir registro.',?)",
                           (item_id,run_id,resource_id,rid,captured))
                db.execute("UPDATE runs SET error_count=error_count+1 WHERE id=?",(run_id,))


def jsonable(row):
    return dict(row) if row else None


class Handler(BaseHTTPRequestHandler):
    server_version = "AurumLexLocal/0.1"

    def log_message(self, fmt, *args):
        # Avoid logging query strings, request bodies, or user-supplied URLs.
        print(f"{self.log_date_time_string()} {self.command} {self.path.split('?')[0]}")

    def send_json(self, obj, status=200):
        body=json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(body)

    def read_body(self):
        size=int(self.headers.get("Content-Length","0"))
        if size>1024*1024: raise ValueError("Requisição excede limite permitido.")
        return json.loads(self.rfile.read(size) or b"{}")

    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/health":
            self.send_json({"ok":True,"storage":"sqlite-local","database":str(DB_PATH)}); return
        if path=="/api/sources":
            with connect() as db: rows=db.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
            self.send_json([dict(r) for r in rows]); return
        if path=="/api/runs":
            with connect() as db: rows=db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 100").fetchall()
            self.send_json([dict(r) for r in rows]); return
        match=re.fullmatch(r"/api/runs/([a-f0-9-]+)",path)
        if match:
            with connect() as db:
                run=db.execute("SELECT * FROM runs WHERE id=?",(match[1],)).fetchone()
                if not run: self.send_json({"error":"Execução não encontrada."},404); return
                resources=db.execute("SELECT * FROM run_resources WHERE run_id=? ORDER BY rowid",(match[1],)).fetchall()
                items=db.execute("SELECT i.*,d.raw_json,d.tribunal,d.dataset,d.source_url,d.resource_url,d.license,d.captured_at FROM run_items i LEFT JOIN documents d ON d.id=i.document_id WHERE i.run_id=? ORDER BY i.processed_at DESC LIMIT 500",(match[1],)).fetchall()
            result=dict(run); result["resources"]=[dict(x) for x in resources]
            result["items"]=[dict(x) for x in items]
            self.send_json(result); return
        if path=="/" or path=="/index.html":
            data=(STATIC_DIR/"index.html").read_bytes(); self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data)))
            self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(data); return
        self.send_json({"error":"Não encontrado."},404)

    def do_POST(self):
        path=urlparse(self.path).path
        try:
            body=self.read_body()
            if path=="/api/connection/test":
                # This standalone MVP explicitly tests only its local SQLite storage.
                with connect() as db: db.execute("SELECT 1").fetchone()
                self.send_json({"ok":True,"message":"Banco local SQLite acessível.","database":"scraper.sqlite3"}); return
            if path=="/api/sources":
                url=safe_public_url(str(body.get("source_url","")))
                parsed=urlparse(url)
                if parsed.path and not parsed.path.startswith("/dataset/") and not re.search(r"\.(json|zip)$",parsed.path,re.I):
                    raise ValueError("Informe uma página de conjunto de dados ou recurso JSON/ZIP oficial do STJ.")
                dataset=str(body.get("dataset") or parsed.path.rstrip("/").split("/")[-1]).replace("espelhos-de-acordaos-","").replace("-"," ")[:120]
                sid=str(uuid.uuid4())
                with connect() as db:
                    db.execute("INSERT INTO sources(id,source_url,tribunal,dataset,created_at) VALUES(?,?,'STJ',?,?)",(sid,url,dataset,now()))
                self.send_json({"id":sid,"source_url":url,"tribunal":"STJ","dataset":dataset},201); return
            if path=="/api/runs":
                ids=body.get("source_ids")
                if not isinstance(ids,list) or not ids: raise ValueError("Selecione ao menos uma fonte.")
                run_id=create_run([str(x) for x in ids])
                self.send_json({"id":run_id,"status":"queued"},202); return
            self.send_json({"error":"Não encontrado."},404)
        except sqlite3.IntegrityError:
            self.send_json({"error":"Essa fonte já está cadastrada."},409)
        except (ValueError,TypeError,json.JSONDecodeError) as exc:
            self.send_json({"error":str(exc)},400)
        except Exception:
            self.send_json({"error":"Falha interna; detalhes não foram expostos."},500)

    def send_error(self, code, message=None, explain=None):
        self.send_json({"error":"Requisição inválida."},code)


def main():
    init_db()
    host=os.environ.get("SCRAPER_HOST","127.0.0.1")
    port=int(os.environ.get("PORT","8000"))
    if host not in ("127.0.0.1","localhost","::1"):
        raise SystemExit("Por segurança, este MVP só pode escutar em loopback. Não exponha sem autenticação e HTTPS.")
    server=ThreadingHTTPServer((host,port),Handler)
    print(f"Aurum Lex — scraper local: http://{host}:{port}")
    print(f"SQLite: {DB_PATH}")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nEncerrando servidor...")
    finally: server.server_close()

if __name__=="__main__": main()
