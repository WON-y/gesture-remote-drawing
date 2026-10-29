"""갤러리: 그림판에서 공유한 그림 저장·목록·삭제.

저장 위치 (GALLERY_DIR, 기본 /data/gallery → 호스트 ./data/gallery):
  images/<id>.png         원본 (edge 가 /gallery-files/ 로 직접 서빙)
  images/<id>.thumb.webp  목록용 썸네일
  gallery.db              SQLite 메타데이터 (edge 에는 마운트하지 않음)

로그인이 없으므로 브라우저가 만든 비밀 owner_id 로 "내 그림"을 구분한다.
서버에는 sha256 해시만 저장하고 응답에는 내보내지 않음 → 남의 그림은 못 지움.
"""
import contextlib
import hashlib
import io
import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from PIL import Image

from common.schema import GalleryItem, GalleryPage

GALLERY_DIR = Path(os.getenv("GALLERY_DIR", "/data/gallery"))
IMAGE_DIR = GALLERY_DIR / "images"
DB_PATH = GALLERY_DIR / "gallery.db"
MAX_BYTES = int(os.getenv("GALLERY_MAX_MB", "10")) * 1024 * 1024  # edge client_max_body_size 와 맞출 것
THUMB_SIZE = 480
FILES_URL = "/gallery-files"

router = APIRouter(prefix="/api/gallery")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_db()) as con, con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS drawings (
                 id TEXT PRIMARY KEY,
                 title TEXT NOT NULL,
                 author TEXT NOT NULL,
                 owner_hash TEXT NOT NULL,
                 created REAL NOT NULL,
                 width INTEGER NOT NULL,
                 height INTEGER NOT NULL)"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_created ON drawings(created DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_owner ON drawings(owner_hash, created DESC)")


def _hash(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode()).hexdigest()


def _paths(did: str) -> tuple[Path, Path]:
    return IMAGE_DIR / f"{did}.png", IMAGE_DIR / f"{did}.thumb.webp"


def _item(row: sqlite3.Row, owner_hash: str | None) -> GalleryItem:
    return GalleryItem(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        created=row["created"],
        width=row["width"],
        height=row["height"],
        url=f"{FILES_URL}/{row['id']}.png",
        thumb_url=f"{FILES_URL}/{row['id']}.thumb.webp",
        mine=owner_hash is not None and row["owner_hash"] == owner_hash,
    )


# sqlite3·Pillow 가 동기 API 라 일반 def 로 둠 → FastAPI 가 스레드풀에서 실행
@router.post("", response_model=GalleryItem)
def upload(
    image: UploadFile = File(...),
    title: str = Form("", max_length=40),
    author: str = Form("", max_length=20),
    owner_id: str = Form(..., min_length=16, max_length=128),
):
    data = image.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "image too large")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG":
                raise ValueError("not png")
            im.load()  # 끝까지 디코딩해 잘린 파일 거름
            width, height = im.size
            # 투명 부분이 검게 나오지 않도록 흰 배경에 합성
            thumb = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im.convert("RGBA")).convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(415, "PNG image required")
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))

    did = uuid.uuid4().hex
    png, webp = _paths(did)
    png.write_bytes(data)
    thumb.save(webp, "WEBP", quality=85)

    owner_hash = _hash(owner_id)
    row = {
        "id": did,
        "title": title.strip() or "무제",
        "author": author.strip() or "익명",
        "owner_hash": owner_hash,
        "created": time.time(),
        "width": width,
        "height": height,
    }
    try:
        with contextlib.closing(_db()) as con, con:
            con.execute(
                "INSERT INTO drawings VALUES (:id, :title, :author, :owner_hash, :created, :width, :height)", row
            )
    except sqlite3.Error:
        png.unlink(missing_ok=True)
        webp.unlink(missing_ok=True)
        raise
    return _item(row, owner_hash)


@router.get("", response_model=GalleryPage)
def list_drawings(
    mine: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    x_owner_id: str | None = Header(None),
):
    owner_hash = _hash(x_owner_id) if x_owner_id else None
    if mine and not owner_hash:
        return GalleryPage(items=[], total=0)
    where, args = ("WHERE owner_hash = ?", [owner_hash]) if mine else ("", [])
    with contextlib.closing(_db()) as con:
        total = con.execute(f"SELECT COUNT(*) FROM drawings {where}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM drawings {where} ORDER BY created DESC LIMIT ? OFFSET ?", [*args, limit, offset]
        ).fetchall()
    return GalleryPage(items=[_item(r, owner_hash) for r in rows], total=total)


@router.delete("/{did}")
def delete(did: str, x_owner_id: str = Header(...)):
    with contextlib.closing(_db()) as con, con:
        row = con.execute("SELECT owner_hash FROM drawings WHERE id = ?", [did]).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        if row["owner_hash"] != _hash(x_owner_id):
            raise HTTPException(403, "not your drawing")
        con.execute("DELETE FROM drawings WHERE id = ?", [did])
    for p in _paths(did):
        p.unlink(missing_ok=True)
    return {"ok": True}
