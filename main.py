from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from copy import copy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import pymupdf
import zipfile
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "pending"
ARCHIVE_DIR = DATA_DIR / "archived"
REIMBURSEMENT_DIR = DATA_DIR / "reimbursements"
DB_PATH = DATA_DIR / "billmanage.db"
REIMBURSEMENT_TEMPLATE = BASE_DIR / "报销单模版.xlsx"
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
OCR_CONFIDENCE_THRESHOLD = 0.82

for directory in (DATA_DIR, UPLOAD_DIR, ARCHIVE_DIR, REIMBURSEMENT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="BillManage")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _add_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS reimbursement_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                submitted_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed'))
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                batch_id INTEGER REFERENCES reimbursement_batches(id),
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                doc_type TEXT CHECK(doc_type IN ('invoice', 'train_ticket', 'didi')),
                amount TEXT,
                expense_date TEXT,
                status TEXT NOT NULL DEFAULT 'unsubmitted'
                    CHECK(status IN ('unsubmitted', 'submitted', 'reimbursed')),
                file_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        _add_column(connection, "documents", "original_filename TEXT")
        _add_column(connection, "documents", "ocr_text TEXT")
        _add_column(connection, "documents", "ocr_data TEXT")
        _add_column(
            connection, "documents", "ocr_status TEXT NOT NULL DEFAULT 'pending'"
        )
        _add_column(connection, "documents", "ocr_error TEXT")
        _add_column(connection, "documents", "archived_at TEXT")
        _add_column(connection, "reimbursement_batches", "form_filename TEXT")
        _add_column(connection, "reimbursement_batches", "form_path TEXT")
        connection.execute(
            "UPDATE documents SET original_filename = filename WHERE original_filename IS NULL OR original_filename = ''"
        )
        _migrate_documents_didi(connection)


def _migrate_documents_didi(connection: sqlite3.Connection) -> None:
    """Rebuild the documents table so doc_type CHECK accepts 'didi' on old databases."""
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
    ).fetchone()
    if not table_sql or "'didi'" in (table_sql["sql"] or ""):
        return
    columns = [
        "id",
        "project_id",
        "batch_id",
        "filename",
        "storage_path",
        "doc_type",
        "amount",
        "expense_date",
        "status",
        "file_hash",
        "original_filename",
        "ocr_text",
        "ocr_data",
        "ocr_status",
        "ocr_error",
        "archived_at",
    ]
    connection.execute(
        """
        CREATE TABLE documents_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            batch_id INTEGER REFERENCES reimbursement_batches(id),
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            doc_type TEXT CHECK(doc_type IN ('invoice', 'train_ticket', 'didi')),
            amount TEXT,
            expense_date TEXT,
            status TEXT NOT NULL DEFAULT 'unsubmitted'
                CHECK(status IN ('unsubmitted', 'submitted', 'reimbursed')),
            file_hash TEXT NOT NULL UNIQUE
        )
        """
    )
    for definition in (
        "original_filename TEXT",
        "ocr_text TEXT",
        "ocr_data TEXT",
        "ocr_status TEXT NOT NULL DEFAULT 'pending'",
        "ocr_error TEXT",
        "archived_at TEXT",
    ):
        _add_column(connection, "documents_new", definition)
    column_sql = ", ".join(columns)
    connection.execute(
        f"INSERT INTO documents_new ({column_sql}) SELECT {column_sql} FROM documents"
    )
    connection.execute("DROP TABLE documents")
    connection.execute("ALTER TABLE documents_new RENAME TO documents")


init_db()


def get_project(connection: sqlite3.Connection, project_id: int):
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not project:
        raise HTTPException(404, "项目不存在")
    return project


def safe_name(value: str, fallback: str = "未命名") -> str:
    cleaned = "".join("_" if char in '/\\:*?"<>|' else char for char in value)
    cleaned = re.sub(r"\s+", "", cleaned).strip("._- ")
    return cleaned[:80] or fallback


def unique_target(
    directory: Path, filename: str, document_id: int | None = None
) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    suffix = target.suffix
    stem = target.stem
    marker = f"-{document_id}" if document_id else "-2"
    candidate = directory / f"{stem}{marker}{suffix}"
    counter = 3
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def move_and_rename(
    source: Path, directory: Path, filename: str, document_id: int
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if target.exists() and source.exists() and target.resolve() == source.resolve():
        return target
    target = unique_target(directory, filename, document_id)
    if source.exists():
        shutil.move(source, target)
    return target


def field(value=None, confidence: float = 0.0, source: str = "") -> dict:
    return {"value": value, "confidence": round(confidence, 2), "source": source}


def available_tesseract_languages() -> str:
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    languages = {line.strip() for line in result.stdout.splitlines()[1:]}
    preferred = [
        language for language in ("chi_sim", "eng", "snum") if language in languages
    ]
    return "+".join(preferred)


def tesseract_image(image_path: Path) -> tuple[str, float]:
    languages = available_tesseract_languages()
    if not languages:
        raise RuntimeError("未找到 Tesseract OCR 或可用语言包")
    result = subprocess.run(
        ["tesseract", str(image_path), "stdout", "-l", languages, "--psm", "6", "tsv"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Tesseract OCR 失败")
    words: list[str] = []
    confidences: list[float] = []
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
    current_line = None
    line_words: list[str] = []
    lines: list[str] = []
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        line_key = (
            row.get("page_num"),
            row.get("block_num"),
            row.get("par_num"),
            row.get("line_num"),
        )
        if current_line is not None and line_key != current_line and line_words:
            lines.append(" ".join(line_words))
            line_words = []
        current_line = line_key
        line_words.append(text)
        words.append(text)
        try:
            confidence = float(row.get("conf") or -1)
            if confidence >= 0:
                confidences.append(confidence)
        except ValueError:
            pass
    if line_words:
        lines.append(" ".join(line_words))
    average = (sum(confidences) / len(confidences) / 100) if confidences else 0.45
    return "\n".join(lines or words), max(0.35, min(average, 0.9))


def extract_ocr(path: Path) -> tuple[str, float, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        document = pymupdf.open(path)
        native_text = "\n".join(
            page.get_text("text", sort=True) for page in document[:3]
        ).strip()
        if len(re.sub(r"\s", "", native_text)) >= 20:
            document.close()
            return native_text, 0.99, "PDF文本层"
        texts: list[str] = []
        confidences: list[float] = []
        with tempfile.TemporaryDirectory(prefix="billmanage-ocr-") as temp_dir:
            for page_number, page in enumerate(document[:3]):
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
                image_path = Path(temp_dir) / f"page-{page_number}.png"
                pixmap.save(image_path)
                text, confidence = tesseract_image(image_path)
                texts.append(text)
                confidences.append(confidence)
        document.close()
        return "\n".join(texts).strip(), min(confidences or [0.4]), "Tesseract图像OCR"
    text, confidence = tesseract_image(path)
    return text.strip(), confidence, "Tesseract图像OCR"


def normalized_date(year: str, month: str, day: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def detect_document_type(text: str, base_confidence: float) -> dict:
    didi_keywords = ("滴滴出行", "滴滴", "网约车", "快车", "专车")
    train_keywords = (
        "铁路电子客票",
        "电子客票号",
        "12306",
        "车次",
        "检票口",
        "开车",
        "次列车",
    )
    invoice_keywords = (
        "发票号码",
        "价税合计",
        "销售方",
        "购买方",
        "增值税",
        "发票代码",
    )
    didi_hits = sum(keyword in text for keyword in didi_keywords)
    train_hits = sum(keyword in text for keyword in train_keywords)
    invoice_hits = sum(keyword in text for keyword in invoice_keywords)
    if didi_hits:
        return field(
            "didi", min(0.99, 0.72 + didi_hits * 0.06), "滴滴出行票据关键字"
        )
    if train_hits:
        return field(
            "train_ticket", min(0.99, 0.78 + train_hits * 0.06), "铁路票据关键字"
        )
    if invoice_hits:
        return field("invoice", min(0.98, 0.72 + invoice_hits * 0.05), "发票关键字")
    return field(None, min(base_confidence, 0.48), "未找到票据类型关键字")


def detect_amount(text: str, base_confidence: float) -> dict:
    compact = text.replace(",", "")
    labeled_patterns = (
        (
            r"价税合计[\s\S]{0,40}?(?:小写)?[：:]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)",
            0.98,
            "价税合计",
        ),
        (r"(?:票价|金额|合计)[：:]?\s*[¥￥]?\s*(\d+(?:\.\d{1,2})?)", 0.94, "金额标签"),
    )
    for pattern, confidence, source in labeled_patterns:
        matches = re.findall(pattern, compact, re.IGNORECASE)
        if matches:
            value = Decimal(matches[-1]).quantize(Decimal("0.01"))
            return field(str(value), min(confidence, base_confidence), source)
    currency_values = re.findall(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)", compact)
    if currency_values:
        values = [Decimal(value).quantize(Decimal("0.01")) for value in currency_values]
        return field(str(max(values)), min(0.9, base_confidence), "票面货币金额")
    decimal_values = re.findall(r"(?<!\d)(\d{1,7}\.\d{2})(?!\d)", compact)
    if decimal_values:
        values = [Decimal(value).quantize(Decimal("0.01")) for value in decimal_values]
        return field(str(max(values)), min(0.68, base_confidence), "数字金额候选")
    return field(None, 0.0, "未识别到金额")


def detect_dates(text: str, doc_type: str | None, base_confidence: float) -> dict:
    candidates: list[tuple[str, float, str]] = []
    pattern = re.compile(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?")
    for match in pattern.finditer(text):
        value = normalized_date(*match.groups())
        if not value:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        context = text[max(0, line_start - 30) : min(len(text), line_end + 30)]
        if "开票日期" in context or "填开日期" in context:
            score, source = (
                (0.25, "开票日期（非乘车日期）")
                if doc_type == "train_ticket"
                else (0.99, "开票日期")
            )
        elif any(keyword in context for keyword in ("乘车", "开车", "发车", "日期")):
            score, source = 0.96, "出行/发生日期"
        else:
            score, source = (
                (0.93, "票面乘车日期")
                if doc_type == "train_ticket"
                else (0.72, "票面日期")
            )
        candidates.append((value, min(score, base_confidence), source))
    if not candidates:
        numeric_pattern = re.compile(
            r"(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})"
        )
        for match in numeric_pattern.finditer(text):
            value = normalized_date(*match.groups())
            if value:
                candidates.append((value, min(0.72, base_confidence), "数字日期"))
    if not candidates:
        return field(None, 0.0, "未识别到日期")
    value, confidence, source = max(candidates, key=lambda item: item[1])
    return field(value, confidence, source)


def detect_route(text: str, base_confidence: float) -> dict:
    stations: list[str] = []
    for match in re.finditer(r"([\u4e00-\u9fff]{2,10})站", text):
        station = match.group(1)
        station = re.sub(r"^(?:到达|出发|始发)", "", station)
        if station not in stations and station not in {"火车", "铁路"}:
            stations.append(station)
    if len(stations) >= 2:
        return field(
            f"{stations[0]}-{stations[1]}", min(0.97, base_confidence), "出发站和到达站"
        )
    return field(None, 0.0, "未识别到行程")


def detect_invoice_number(text: str, base_confidence: float) -> dict:
    patterns = (
        (r"发票号码[：:]?\s*([A-Za-z0-9]{8,30})", 0.98, "发票号码"),
        (r"电子客票号[码]?[：:]?\s*([A-Za-z0-9]{8,30})", 0.96, "电子客票号"),
        (r"票号[：:]?\s*([A-Za-z0-9]{8,30})", 0.9, "票号"),
    )
    for pattern, confidence, source in patterns:
        match = re.search(pattern, text)
        if match:
            return field(match.group(1), min(confidence, base_confidence), source)
    return field(None, 0.0, "未识别到发票号码")


def detect_merchant(text: str, base_confidence: float) -> dict:
    patterns = (
        r"销售方(?:信息)?(?:名称)?[：:]\s*([^\n]{2,40})",
        r"销方名称[：:]\s*([^\n]{2,40})",
        r"收款单位[：:]\s*([^\n]{2,40})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            merchant = re.split(r"(?:统一社会信用代码|纳税人识别号)", match.group(1))[
                0
            ].strip()
            return field(merchant, min(0.9, base_confidence), "销售方名称")
    return field(None, 0.0, "未识别到销售方")


def parse_ocr(text: str, base_confidence: float, engine: str) -> dict:
    doc_type = detect_document_type(text, base_confidence)
    parsed = {
        "engine": engine,
        "doc_type": doc_type,
        "amount": detect_amount(text, base_confidence),
        "expense_date": detect_dates(text, doc_type["value"], base_confidence),
        "route": detect_route(text, base_confidence),
        "invoice_number": detect_invoice_number(text, base_confidence),
        "merchant": detect_merchant(text, base_confidence),
    }
    required = (parsed["doc_type"], parsed["amount"], parsed["expense_date"])
    parsed["overall_confidence"] = round(
        min(item["confidence"] for item in required), 2
    )
    return parsed


def suggested_filename(
    data: dict, suffix: str, document_id: int, confirmed: bool = False
) -> str:
    doc_type = data.get("doc_type", {}).get("value")
    expense_date = data.get("expense_date", {}).get("value")
    amount = data.get("amount", {}).get("value")
    parts: list[str] = []
    if expense_date:
        parts.append(expense_date)
    else:
        parts.append("待确认")
    parts.append(
        "滴滴打车"
        if doc_type == "didi"
        else "火车票"
        if doc_type == "train_ticket"
        else "发票"
        if doc_type == "invoice"
        else "票据"
    )
    if doc_type in {"train_ticket", "didi"} and data.get("route", {}).get("value"):
        parts.append(data["route"]["value"])
    elif data.get("merchant", {}).get("value"):
        parts.append(data["merchant"]["value"])
    elif data.get("invoice_number", {}).get("value"):
        parts.append(data["invoice_number"]["value"][-10:])
    if amount:
        parts.append(amount)
    if not confirmed and data.get("overall_confidence", 0) < OCR_CONFIDENCE_THRESHOLD:
        parts.insert(0, "待核对")
    name = "_".join(safe_name(str(part)) for part in parts if part)
    return f"{name or f'票据-{document_id}'}{suffix.lower()}"


def process_document_ocr(
    connection: sqlite3.Connection,
    document_id: int,
    preserve_confirmation: bool = False,
) -> None:
    document = connection.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not document:
        raise HTTPException(404, "票据不存在")
    path = BASE_DIR / document["storage_path"]
    try:
        text, base_confidence, engine = extract_ocr(path)
        parsed = parse_ocr(text, base_confidence, engine)
        complete = all(
            parsed[key]["value"] for key in ("doc_type", "amount", "expense_date")
        )
        ocr_status = (
            "ready"
            if complete and parsed["overall_confidence"] >= OCR_CONFIDENCE_THRESHOLD
            else "needs_review"
        )
        filename = suggested_filename(parsed, path.suffix, document_id)
        target = move_and_rename(path, path.parent, filename, document_id)
        values = (
            filename,
            str(target.relative_to(BASE_DIR)),
            parsed["doc_type"]["value"],
            parsed["amount"]["value"],
            parsed["expense_date"]["value"],
            text,
            json.dumps(parsed, ensure_ascii=False),
            ocr_status,
            None,
            document_id,
        )
        connection.execute(
            """
            UPDATE documents
            SET filename = ?, storage_path = ?, doc_type = ?, amount = ?, expense_date = ?,
                ocr_text = ?, ocr_data = ?, ocr_status = ?, ocr_error = ?
            WHERE id = ?
            """,
            values,
        )
        if preserve_confirmation and complete:
            finalize_document(connection, document_id, parsed, mark_confirmed=True)
    except Exception as exc:  # OCR failures must not lose the uploaded original.
        connection.execute(
            "UPDATE documents SET ocr_status = 'failed', ocr_error = ? WHERE id = ?",
            (str(exc)[:500], document_id),
        )


def finalize_document(
    connection: sqlite3.Connection,
    document_id: int,
    parsed: dict,
    mark_confirmed: bool = True,
) -> None:
    document = connection.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not document:
        raise HTTPException(404, "票据不存在")
    project = get_project(connection, document["project_id"])
    expense_date = parsed["expense_date"]["value"]
    filename = suggested_filename(
        parsed, Path(document["filename"]).suffix, document_id, confirmed=True
    )
    target_dir = (
        ARCHIVE_DIR / safe_name(project["name"], "未命名项目") / expense_date[:7]
    )
    source = BASE_DIR / document["storage_path"]
    target = move_and_rename(source, target_dir, filename, document_id)
    connection.execute(
        """
        UPDATE documents
        SET filename = ?, storage_path = ?, doc_type = ?, amount = ?, expense_date = ?,
            ocr_data = ?, ocr_status = ?, ocr_error = NULL, archived_at = ?
        WHERE id = ?
        """,
        (
            filename,
            str(target.relative_to(BASE_DIR)),
            parsed["doc_type"]["value"],
            parsed["amount"]["value"],
            expense_date,
            json.dumps(parsed, ensure_ascii=False),
            "confirmed" if mark_confirmed else "ready",
            datetime.now().isoformat(timespec="seconds") if mark_confirmed else None,
            document_id,
        ),
    )


def archive_document(connection: sqlite3.Connection, document_id: int) -> None:
    """Move a reimbursed document into the archived directory and rename it."""
    document = require_document(connection, document_id)
    if document["archived_at"]:
        return
    try:
        parsed = json.loads(document["ocr_data"] or "{}")
    except json.JSONDecodeError:
        parsed = {}
    expense_date = document["expense_date"]
    if not expense_date:
        return
    project = get_project(connection, document["project_id"])
    filename = suggested_filename(
        parsed, Path(document["filename"]).suffix, document_id, confirmed=True
    )
    target_dir = (
        ARCHIVE_DIR / safe_name(project["name"], "未命名项目") / expense_date[:7]
    )
    source = BASE_DIR / document["storage_path"]
    target = move_and_rename(source, target_dir, filename, document_id)
    connection.execute(
        """
        UPDATE documents
        SET filename = ?, storage_path = ?, archived_at = ?
        WHERE id = ?
        """,
        (
            filename,
            str(target.relative_to(BASE_DIR)),
            datetime.now().isoformat(timespec="seconds"),
            document_id,
        ),
    )


def document_view(row: sqlite3.Row) -> dict:
    item = dict(row)
    try:
        data = json.loads(item.get("ocr_data") or "{}")
    except json.JSONDecodeError:
        data = {}
    for key in (
        "doc_type",
        "amount",
        "expense_date",
        "route",
        "invoice_number",
        "merchant",
    ):
        current = data.get(key)
        if not isinstance(current, dict):
            data[key] = field(
                item.get(key)
                if key in {"doc_type", "amount", "expense_date"}
                else None,
                0.0,
                "",
            )
        else:
            data.setdefault(key, field(None, 0.0, ""))
            if data[key].get("value") in (None, "") and key in {
                "doc_type",
                "amount",
                "expense_date",
            }:
                data[key] = field(
                    item.get(key),
                    data[key].get("confidence") or 0.0,
                    data[key].get("source") or "",
                )
    item["ocr"] = data
    item["file_url"] = f"/documents/{item['id']}/file?inline=1"
    item["is_pdf"] = Path(item["filename"]).suffix.lower() == ".pdf"
    locked = item["status"] in {"submitted", "reimbursed"}
    item["locked"] = locked
    item["can_edit"] = (not locked) and item["ocr_status"] != "pending"
    item["can_confirm"] = item["can_edit"]
    item["can_rerun"] = not locked
    item["can_delete"] = item["status"] == "unsubmitted"
    item["eligible"] = (
        item["status"] == "unsubmitted" and item["ocr_status"] == "confirmed"
    )
    item["invoice_number"] = data.get("invoice_number", {}).get("value") or ""
    item["route"] = data.get("route", {}).get("value") or ""
    item["merchant"] = data.get("merchant", {}).get("value") or ""
    return item


def ocr_value(document: sqlite3.Row | dict, key: str) -> str:
    raw = (
        document["ocr_data"]
        if not isinstance(document, dict)
        else document.get("ocr_data")
    )
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return ""
    value = data.get(key, {}).get("value")
    return str(value).strip() if value else ""


def expense_category(doc_type: str | None) -> str:
    if doc_type == "didi":
        return "交通费"
    if doc_type == "train_ticket":
        return "交通费"
    if doc_type == "invoice":
        return "发票"
    return "其他"


def expense_detail(document: sqlite3.Row | dict) -> str:
    route = ocr_value(document, "route")
    merchant = ocr_value(document, "merchant")
    if route:
        return route
    if merchant:
        return merchant
    filename = (
        document["filename"]
        if not isinstance(document, dict)
        else document.get("filename", "")
    )
    return Path(filename).stem


def copy_cell_style(source, target) -> None:
    if source.has_style:
        target._style = copy(source._style)
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.alignment = copy(source.alignment)
    target.protection = copy(source.protection)
    target.number_format = source.number_format


def insert_template_rows(worksheet, row: int, amount: int) -> None:
    """Insert detail rows above the total row while preserving merges and styles."""
    if amount <= 0:
        return

    merged_ranges = [str(cell_range) for cell_range in worksheet.merged_cells.ranges]
    for cell_range in merged_ranges:
        worksheet.unmerge_cells(cell_range)

    # Preserve row heights that will shift down.
    old_heights = {
        idx: worksheet.row_dimensions[idx].height
        for idx in range(row, worksheet.max_row + 1)
        if worksheet.row_dimensions[idx].height is not None
    }

    worksheet.insert_rows(row, amount)

    for cell_range in merged_ranges:
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)
        if min_row >= row:
            min_row += amount
            max_row += amount
        worksheet.merge_cells(
            f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"
        )

    for old_idx, height in sorted(old_heights.items(), reverse=True):
        worksheet.row_dimensions[old_idx + amount].height = height

    source_row = row - 1
    for target_row in range(row, row + amount):
        worksheet.row_dimensions[target_row].height = worksheet.row_dimensions[
            source_row
        ].height
        for column in range(1, worksheet.max_column + 1):
            copy_cell_style(
                worksheet.cell(source_row, column), worksheet.cell(target_row, column)
            )


def clear_detail_row(worksheet, row_number: int) -> None:
    for column in range(1, 8):
        worksheet.cell(row_number, column).value = None


def generate_reimbursement_form(connection: sqlite3.Connection, batch_id: int) -> Path:
    batch = connection.execute(
        """
        SELECT b.*, p.name AS project_name
        FROM reimbursement_batches b
        JOIN projects p ON p.id = b.project_id
        WHERE b.id = ?
        """,
        (batch_id,),
    ).fetchone()
    if not batch:
        raise HTTPException(404, "报销批次不存在")

    documents = connection.execute(
        """
        SELECT * FROM documents
        WHERE batch_id = ?
        ORDER BY expense_date, id
        """,
        (batch_id,),
    ).fetchall()
    if not documents:
        raise HTTPException(400, "报销批次中没有票据")
    if not REIMBURSEMENT_TEMPLATE.exists():
        raise HTTPException(500, "未找到报销单模版.xlsx")

    workbook = load_workbook(REIMBURSEMENT_TEMPLATE)
    worksheet = (
        workbook["Sheet1"] if "Sheet1" in workbook.sheetnames else workbook.active
    )

    detail_start = 9
    template_detail_rows = 3
    total_row = 12
    extra_rows = max(0, len(documents) - template_detail_rows)
    insert_template_rows(worksheet, total_row, extra_rows)

    detail_rows = max(template_detail_rows, len(documents))
    detail_end = detail_start + detail_rows - 1
    total_row = detail_end + 1

    expense_dates = [
        date.fromisoformat(document["expense_date"])
        for document in documents
        if document["expense_date"]
    ]
    if not expense_dates:
        raise HTTPException(400, "票据缺少费用日期，无法生成报销单")
    first_date = min(expense_dates)
    last_date = max(expense_dates)
    worksheet["C4"] = (
        first_date.isoformat()
        if first_date == last_date
        else f"{first_date.isoformat()} 至 {last_date.isoformat()}"
    )

    locations: list[str] = []
    for document in documents:
        route = ocr_value(document, "route")
        if not route:
            continue
        for location in re.split(r"[-–—~～至到]", route):
            location = location.strip()
            if location and location not in locations:
                locations.append(location)
    worksheet["C5"] = "、".join(locations) if locations else ""
    worksheet["C6"] = batch["project_name"]

    for row_number in range(detail_start, detail_end + 1):
        clear_detail_row(worksheet, row_number)

    total_amount = Decimal("0.00")
    for index, document in enumerate(documents, start=1):
        row_number = detail_start + index - 1
        amount = Decimal(str(document["amount"] or "0")).quantize(Decimal("0.01"))
        total_amount += amount
        invoice_number = ocr_value(document, "invoice_number")

        worksheet.cell(row_number, 1, index)
        worksheet.cell(row_number, 2, expense_category(document["doc_type"]))
        worksheet.cell(row_number, 3, expense_detail(document))
        amount_cell = worksheet.cell(row_number, 4, float(amount))
        amount_cell.number_format = "0.00"
        date_cell = worksheet.cell(
            row_number, 5, date.fromisoformat(document["expense_date"])
        )
        date_cell.number_format = "yyyy-mm-dd"
        worksheet.cell(row_number, 6, invoice_number or None)
        worksheet.cell(row_number, 7, None)

    # Keep the shifted "合计" label if present; otherwise write it.
    if not worksheet.cell(total_row, 1).value:
        worksheet.cell(total_row, 1, "合计")
    total_cell = worksheet.cell(total_row, 4, float(total_amount))
    total_cell.number_format = "0.00"
    # Also keep a formula-compatible note in case users insert rows later in Excel.
    worksheet.cell(total_row, 4).value = float(total_amount)

    worksheet.freeze_panes = "A9"
    worksheet.sheet_view.showGridLines = False
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True

    target_dir = REIMBURSEMENT_DIR / safe_name(batch["project_name"], "未命名项目")
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{batch['submitted_date']}_{safe_name(batch['project_name'], '项目')}_"
        f"报销单_批次{batch_id}.xlsx"
    )
    target = target_dir / filename
    if target.exists():
        target.unlink()
    workbook.save(target)
    connection.execute(
        "UPDATE reimbursement_batches SET form_filename = ?, form_path = ? WHERE id = ?",
        (filename, str(target.relative_to(BASE_DIR)), batch_id),
    )
    return target


def list_eligible_documents(connection: sqlite3.Connection, project_id: int):
    return connection.execute(
        """
        SELECT * FROM documents
        WHERE project_id = ?
          AND status = 'unsubmitted'
          AND ocr_status = 'confirmed'
        ORDER BY expense_date, id
        """,
        (project_id,),
    ).fetchall()


def batch_document_total(documents) -> str:
    total = Decimal("0.00")
    for document in documents:
        try:
            total += Decimal(str(document["amount"] or "0"))
        except InvalidOperation:
            continue
    return f"{total.quantize(Decimal('0.01'))}"


def require_document(connection: sqlite3.Connection, document_id: int):
    document = connection.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not document:
        raise HTTPException(404, "票据不存在")
    return document


@app.get("/")
def home(request: Request):
    with db() as connection:
        projects = connection.execute(
            """
            SELECT p.*, COUNT(d.id) AS document_count,
                   COALESCE(SUM(CASE WHEN d.status = 'unsubmitted' THEN 1 ELSE 0 END), 0) AS todo_count
            FROM projects p LEFT JOIN documents d ON d.project_id = p.id
            GROUP BY p.id ORDER BY p.id DESC
            """
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"projects": projects, "current_project_id": None},
    )


@app.post("/projects")
def create_project(name: str = Form(...)):
    name = name.strip()
    if not name:
        return RedirectResponse("/?error=项目名称不能为空", 303)
    try:
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO projects(name) VALUES (?)", (name,)
            )
            project_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return RedirectResponse("/?error=项目名称已存在", 303)
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.post("/template")
def upload_template(
    template: UploadFile = File(...),
    project_id: int = Form(default=0),
):
    """上传新的报销单模板（.xlsx），旧模板自动备份为 报销单模版.bak.xlsx。"""
    filename = Path(template.filename or "").name
    redirect_to = f"/projects/{project_id}" if project_id else "/"
    if not filename.lower().endswith(".xlsx"):
        return RedirectResponse(f"{redirect_to}?template_error=仅支持 .xlsx 格式的模板", 303)
    content = template.file.read()
    try:
        workbook = load_workbook(io.BytesIO(content))
        if not workbook.sheetnames:
            raise ValueError("模板没有工作表")
    except Exception:
        return RedirectResponse(
            f"{redirect_to}?template_error=模板文件无法打开，请确认是有效的 Excel 文件", 303
        )
    REIMBURSEMENT_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if REIMBURSEMENT_TEMPLATE.exists():
        shutil.copy2(REIMBURSEMENT_TEMPLATE, REIMBURSEMENT_TEMPLATE.with_name("报销单模版.bak.xlsx"))
    REIMBURSEMENT_TEMPLATE.write_bytes(content)
    return RedirectResponse(f"{redirect_to}?template_uploaded=1", 303)


@app.get("/projects/{project_id}")
def project_detail(request: Request, project_id: int, document: int | None = None):
    with db() as connection:
        project = get_project(connection, project_id)
        pending_ocr = connection.execute(
            "SELECT id, expense_date, ocr_text FROM documents WHERE project_id = ? AND ocr_status = 'pending'",
            (project_id,),
        ).fetchall()
        for pending in pending_ocr:
            legacy_confirmed = bool(pending["expense_date"] and not pending["ocr_text"])
            process_document_ocr(
                connection, pending["id"], preserve_confirmation=legacy_confirmed
            )
        rows = connection.execute(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        documents = [document_view(row) for row in rows]
        pending_batch = connection.execute(
            """
            SELECT * FROM reimbursement_batches
            WHERE project_id = ? AND status = 'pending'
            ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        sidebar_projects = connection.execute(
            """
            SELECT p.*,
                   COUNT(d.id) AS document_count,
                   COALESCE(SUM(CASE WHEN d.status = 'unsubmitted' THEN 1 ELSE 0 END), 0) AS todo_count
            FROM projects p
            LEFT JOIN documents d ON d.project_id = p.id
            GROUP BY p.id
            ORDER BY p.id DESC
            """
        ).fetchall()
        batches = connection.execute(
            """
            SELECT b.*,
                   COUNT(d.id) AS document_count,
                   COALESCE(SUM(CAST(d.amount AS REAL)), 0) AS total_amount
            FROM reimbursement_batches b
            LEFT JOIN documents d ON d.batch_id = b.id
            WHERE b.project_id = ?
            GROUP BY b.id
            ORDER BY b.id DESC
            """,
            (project_id,),
        ).fetchall()
        eligible = list_eligible_documents(connection, project_id)

    stats = {
        "unsubmitted": sum(d["status"] == "unsubmitted" for d in documents),
        "submitted": sum(d["status"] == "submitted" for d in documents),
        "reimbursed": sum(d["status"] == "reimbursed" for d in documents),
        "review": sum(
            d["ocr_status"] in {"ready", "needs_review", "failed"} for d in documents
        ),
        "eligible": len(eligible),
        "eligible_total": batch_document_total(eligible),
    }
    selected_id = document if any(d["id"] == document for d in documents) else None
    if selected_id is None and documents:
        selected_id = next(
            (
                d["id"]
                for d in documents
                if d["can_edit"] and d["ocr_status"] != "confirmed"
            ),
            next((d["id"] for d in documents if d["can_edit"]), documents[0]["id"]),
        )
    documents_json = {str(item["id"]): item for item in documents}
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "project": project,
            "documents": documents,
            "documents_json": documents_json,
            "pending_batch": pending_batch,
            "batches": batches,
            "stats": stats,
            "selected_id": selected_id,
            "confidence_threshold": OCR_CONFIDENCE_THRESHOLD,
            "projects": sidebar_projects,
            "current_project_id": project_id,
        },
    )


@app.post("/projects/{project_id}/upload")
async def upload_documents(project_id: int, files: list[UploadFile] = File(...)):
    uploaded_ids: list[int] = []
    with db() as connection:
        get_project(connection, project_id)
        for upload in files:
            original_name = Path(upload.filename or "upload.bin").name
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                continue
            content = await upload.read()
            digest = hashlib.sha256(content).hexdigest()
            if connection.execute(
                "SELECT 1 FROM documents WHERE file_hash = ?", (digest,)
            ).fetchone():
                continue
            stored_name = f"{uuid.uuid4().hex}{suffix}"
            target = UPLOAD_DIR / stored_name
            target.write_bytes(content)
            cursor = connection.execute(
                """
                INSERT INTO documents(project_id, filename, original_filename, storage_path, file_hash, ocr_status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (
                    project_id,
                    original_name,
                    original_name,
                    str(target.relative_to(BASE_DIR)),
                    digest,
                ),
            )
            uploaded_ids.append(cursor.lastrowid)
        for document_id in uploaded_ids:
            process_document_ocr(connection, document_id)
    query = f"?document={uploaded_ids[0]}#receipt-workbench" if uploaded_ids else ""
    return RedirectResponse(f"/projects/{project_id}{query}", 303)


@app.post("/documents/{document_id}/ocr")
def rerun_document_ocr(document_id: int):
    with db() as connection:
        document = require_document(connection, document_id)
        if document["status"] != "unsubmitted":
            return RedirectResponse(
                f"/projects/{document['project_id']}?error=已提交或已报销的票据不能重新识别",
                303,
            )
        process_document_ocr(connection, document_id)
        project_id = document["project_id"]
    return RedirectResponse(
        f"/projects/{project_id}?document={document_id}#receipt-workbench", 303
    )


@app.post("/documents/{document_id}/confirm")
def confirm_document(
    document_id: int,
    doc_type: str = Form(...),
    amount: str = Form(...),
    expense_date: str = Form(...),
    route: str = Form(default=""),
    merchant: str = Form(default=""),
    invoice_number: str = Form(default=""),
):
    if doc_type not in {"invoice", "train_ticket", "didi"}:
        raise HTTPException(400, "票据类型无效")
    try:
        normalized_amount = str(Decimal(amount).quantize(Decimal("0.01")))
        date.fromisoformat(expense_date)
    except (InvalidOperation, ValueError):
        raise HTTPException(400, "金额或日期格式无效")
    with db() as connection:
        document = require_document(connection, document_id)
        if document["status"] != "unsubmitted":
            return RedirectResponse(
                f"/projects/{document['project_id']}?error=已提交或已报销的票据不能修改",
                303,
            )
        try:
            parsed = json.loads(document["ocr_data"] or "{}")
        except json.JSONDecodeError:
            parsed = {}
        parsed["doc_type"] = field(doc_type, 1.0, "人工确认")
        parsed["amount"] = field(normalized_amount, 1.0, "人工确认")
        parsed["expense_date"] = field(expense_date, 1.0, "人工确认")
        parsed["route"] = field(
            route.strip() or None, 1.0 if route.strip() else 0.0, "人工确认"
        )
        parsed["merchant"] = field(
            merchant.strip() or None, 1.0 if merchant.strip() else 0.0, "人工确认"
        )
        parsed["invoice_number"] = field(
            invoice_number.strip() or None,
            1.0 if invoice_number.strip() else 0.0,
            "人工确认",
        )
        parsed["overall_confidence"] = 1.0
        connection.execute(
            """
            UPDATE documents
            SET doc_type = ?, amount = ?, expense_date = ?,
                ocr_data = ?, ocr_status = 'confirmed', ocr_error = NULL
            WHERE id = ?
            """,
            (
                doc_type,
                normalized_amount,
                expense_date,
                json.dumps(parsed, ensure_ascii=False),
                document_id,
            ),
        )
        project_id = document["project_id"]
    return RedirectResponse(
        f"/projects/{project_id}?document={document_id}#receipt-workbench", 303
    )


@app.get("/documents/{document_id}/file")
def document_file(document_id: int, inline: bool = False):
    with db() as connection:
        document = require_document(connection, document_id)
    path = BASE_DIR / document["storage_path"]
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(document['filename'])}"
    }
    return FileResponse(path, media_type=media_type, headers=headers)


@app.post("/documents/{document_id}/delete")
def delete_document(document_id: int):
    files_to_delete: list[Path] = []
    with db() as connection:
        document = require_document(connection, document_id)
        if document["status"] != "unsubmitted":
            return RedirectResponse(
                f"/projects/{document['project_id']}?error=已提交或已报销的票据不能删除，请先撤回报销单",
                303,
            )
        project_id = document["project_id"]
        files_to_delete.append(BASE_DIR / document["storage_path"])
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    for path in files_to_delete:
        path.unlink(missing_ok=True)
    return RedirectResponse(f"/projects/{project_id}", 303)


@app.post("/projects/{project_id}/submit")
def submit_batch(project_id: int, document_ids: list[int] = Form(default=[])):
    with db() as connection:
        get_project(connection, project_id)
        if connection.execute(
            "SELECT 1 FROM reimbursement_batches WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        ).fetchone():
            return RedirectResponse(
                f"/projects/{project_id}?error=请先确认或撤回上一次报销单", 303
            )

        selected_ids = []
        seen = set()
        for value in document_ids:
            try:
                doc_id = int(value)
            except (TypeError, ValueError):
                continue
            if doc_id in seen:
                continue
            seen.add(doc_id)
            selected_ids.append(doc_id)

        if not selected_ids:
            return RedirectResponse(
                f"/projects/{project_id}?error=请先勾选要报销的票据",
                303,
            )

        placeholders = ",".join("?" for _ in selected_ids)
        selected = connection.execute(
            f"""
            SELECT * FROM documents
            WHERE project_id = ?
              AND id IN ({placeholders})
              AND status = 'unsubmitted'
              AND ocr_status = 'confirmed'
            ORDER BY expense_date, id
            """,
            (project_id, *selected_ids),
        ).fetchall()
        if len(selected) != len(selected_ids):
            return RedirectResponse(
                f"/projects/{project_id}?error=只能选择已确认归档且未报销的票据",
                303,
            )

        cursor = connection.execute(
            "INSERT INTO reimbursement_batches(project_id, submitted_date, status) VALUES (?, ?, 'pending')",
            (project_id, date.today().isoformat()),
        )
        batch_id = cursor.lastrowid
        connection.execute(
            f"""
            UPDATE documents
            SET batch_id = ?, status = 'submitted'
            WHERE id IN ({placeholders})
            """,
            (batch_id, *selected_ids),
        )
        generate_reimbursement_form(connection, batch_id)
    return RedirectResponse(f"/projects/{project_id}?generated=1", 303)


@app.get("/batches/{batch_id}/form")
def reimbursement_form(batch_id: int):
    with db() as connection:
        batch = connection.execute(
            "SELECT * FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch:
            raise HTTPException(404, "报销批次不存在")
        path = BASE_DIR / batch["form_path"] if batch["form_path"] else None
        if not path or not path.exists():
            path = generate_reimbursement_form(connection, batch_id)
        filename = connection.execute(
            "SELECT form_filename FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()["form_filename"]
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/batches/{batch_id}/package")
def batch_package(batch_id: int):
    """打包导出：报销单 Excel + 该批次全部票据（使用整理后的归档文件名）。"""
    with db() as connection:
        batch = connection.execute(
            "SELECT * FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch:
            raise HTTPException(404, "报销批次不存在")
        form_path = BASE_DIR / batch["form_path"] if batch["form_path"] else None
        if not form_path or not form_path.exists():
            form_path = generate_reimbursement_form(connection, batch_id)
        form_filename = connection.execute(
            "SELECT form_filename FROM reimbursement_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()["form_filename"]
        documents = connection.execute(
            """
            SELECT filename, storage_path FROM documents
            WHERE batch_id = ?
            ORDER BY expense_date, id
            """,
            (batch_id,),
        ).fetchall()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(form_path, arcname=form_filename or form_path.name)
        used_names: dict[str, int] = {}
        for document in documents:
            path = BASE_DIR / document["storage_path"]
            if not path.exists():
                continue
            name = document["filename"]
            if name in used_names:
                used_names[name] += 1
                stem, suffix = Path(name).stem, Path(name).suffix
                name = f"{stem}-{used_names[name]}{suffix}"
            else:
                used_names[name] = 0
            archive.write(path, arcname=f"票据/{name}")
    buffer.seek(0)
    zip_name = f"{Path(form_filename).stem}_报销单与票据.zip"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}"}
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers=headers,
    )


@app.post("/batches/{batch_id}/resolve")
def resolve_batch(batch_id: int, returned_ids: list[int] = Form(default=[])):
    with db() as connection:
        batch = connection.execute(
            "SELECT * FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch or batch["status"] != "pending":
            raise HTTPException(404, "待确认批次不存在")

        connection.execute(
            "UPDATE documents SET status = 'reimbursed' WHERE batch_id = ? AND status = 'submitted'",
            (batch_id,),
        )
        if returned_ids:
            placeholders = ",".join("?" for _ in returned_ids)
            connection.execute(
                f"""
                UPDATE documents
                SET status = 'unsubmitted', batch_id = NULL
                WHERE batch_id = ? AND id IN ({placeholders})
                """,
                (batch_id, *returned_ids),
            )
            remaining = connection.execute(
                "SELECT COUNT(*) FROM documents WHERE batch_id = ?", (batch_id,)
            ).fetchone()[0]
            if remaining:
                generate_reimbursement_form(connection, batch_id)
        connection.execute(
            "UPDATE reimbursement_batches SET status = 'confirmed' WHERE id = ?",
            (batch_id,),
        )
        reimbursed = connection.execute(
            """
            SELECT id FROM documents
            WHERE batch_id = ? AND status = 'reimbursed'
            """,
            (batch_id,),
        ).fetchall()
        for row in reimbursed:
            archive_document(connection, row["id"])
    return RedirectResponse(f"/projects/{batch['project_id']}?resolved=1", 303)


@app.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: int):
    files_to_delete: list[Path] = []
    with db() as connection:
        batch = connection.execute(
            "SELECT * FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch or batch["status"] != "pending":
            raise HTTPException(404, "待确认批次不存在")
        connection.execute(
            """
            UPDATE documents
            SET status = 'unsubmitted', batch_id = NULL
            WHERE batch_id = ? AND status = 'submitted'
            """,
            (batch_id,),
        )
        if batch["form_path"]:
            files_to_delete.append(BASE_DIR / batch["form_path"])
        connection.execute(
            "DELETE FROM reimbursement_batches WHERE id = ?", (batch_id,)
        )
        project_id = batch["project_id"]
    for path in files_to_delete:
        path.unlink(missing_ok=True)
    return RedirectResponse(f"/projects/{project_id}?cancelled=1", 303)


@app.post("/batches/{batch_id}/revert")
def revert_batch(batch_id: int):
    with db() as connection:
        batch = connection.execute(
            "SELECT * FROM reimbursement_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch or batch["status"] != "confirmed":
            raise HTTPException(404, "已确认批次不存在")
        project_id = batch["project_id"]
        connection.execute(
            """
            UPDATE documents
            SET status = 'unsubmitted', batch_id = NULL
            WHERE batch_id = ?
            """,
            (batch_id,),
        )
        form_path = batch["form_path"]
        connection.execute(
            "DELETE FROM reimbursement_batches WHERE id = ?", (batch_id,)
        )
    if form_path:
        (BASE_DIR / form_path).unlink(missing_ok=True)
    return RedirectResponse(f"/projects/{project_id}?reverted=1", 303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
