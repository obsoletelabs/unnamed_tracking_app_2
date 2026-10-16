"""A game's Docs tab takes any file type, under its own size cap, and only ever
returns the stored bytes as an inert attachment (issue #157)."""

import io
import os
import uuid
import zipfile
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from src.core.app_integrations import get_or_create_app_integration_settings
from src.core.auth import get_current_user
from src.database.session import SessionLocal
from src.main import app
from src.plugin_api.documents import OFFICE_TYPES, read_representation
from tests.test_game_files_flow import game_flow

flow = game_flow

MB = 1024 * 1024
DOCX = OFFICE_TYPES[".docx"]


def _docx(padding: int = 0) -> bytes:
    """A minimal Word document; ``padding`` adds a stored picture of that many bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            f'<Override PartName="/word/document.xml" ContentType="{DOCX}.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Boss guide</w:t></w:r></w:p></w:body></w:document>",
        )
        if padding:
            archive.writestr(
                zipfile.ZipInfo("word/media/image1.png"), os.urandom(padding), zipfile.ZIP_STORED
            )
    return buffer.getvalue()


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "content")
    return buffer.getvalue()


# Ordinary documents of every kind, plus files a browser would run or render if
# it were ever served as itself. Docs keep all of them, byte for byte.
ANY_FILES = [
    ("Walkthrough.docx", _docx(padding=MB + MB // 2), DOCX),
    ("攻略 — guide.docx", _docx(), "application/octet-stream"),
    ("Old manual.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512, "application/msword"),
    ("Routes.xlsx", _zip("[Content_Types].xml", "xl/workbook.xml"), "application/vnd.ms-excel"),
    ("Slides.pptx", _zip("[Content_Types].xml", "ppt/presentation.xml"), ""),
    ("Notes.odt", _zip("mimetype", "content.xml"), "application/vnd.oasis.opendocument.text"),
    ("Manual.pdf", b"%PDF-1.7\n%%EOF", "application/pdf"),
    ("Readme.rtf", rb"{\rtf1\ansi Boss guide}", "application/rtf"),
    ("Lore.epub", _zip("mimetype", "META-INF/container.xml"), "application/epub+zip"),
    ("Map.html", b"<script>alert(document.cookie)</script>", "text/html"),
    ("Icon.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "image/svg+xml"),
    ("Tool.exe", b"MZ\x90\x00" + os.urandom(256), "application/x-msdownload"),
    ("LICENSE", b"no extension at all", "application/octet-stream"),
    ("empty.txt", b"", "text/plain"),
]


async def test_docs_accept_any_file_type_and_return_it_only_as_an_attachment(flow) -> None:
    response = await flow.client.post(
        f"{flow.game}/files/doc", files=[("files", item) for item in ANY_FILES]
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [r["status"] for r in results] == ["saved"] * len(ANY_FILES)
    assert [r["size"] for r in results] == [len(data) for _, data, _ in ANY_FILES]

    listed = {
        f["original_filename"]: f
        for f in (await flow.client.get(f"{flow.game}/files/doc")).json()["files"]
    }
    assert set(listed) == {name for name, _, _ in ANY_FILES}
    for name, data, _ in ANY_FILES:
        item = listed[name]
        assert item["size"] == len(data)
        # Stored under a sanitised, collision-proof name inside the game's own docs folder.
        assert "/" not in item["filename"] and item["filename"] != name
        download = await flow.client.get(item["url"])
        assert download.status_code == 200
        assert download.content == data
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["content-security-policy"] == "sandbox"
        disposition = download.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert name in unquote(disposition)

    # The Word documents are real ones: the Plugin API reading preview opens them.
    stored = flow.tmp / str(flow.user_id) / "games" / "Flow" / "docs"
    for name in ("Walkthrough.docx", "攻略 — guide.docx"):
        path = stored / listed[name]["filename"]
        assert read_representation(path, 0)[1:3] == (DOCX, "docx")


async def test_docs_stay_in_the_owners_game_folder(flow) -> None:
    response = await flow.client.post(
        f"{flow.game}/files/doc",
        files={"file": ("../../..\\notes/../../Manual.docx", _docx(), DOCX)},
    )
    saved = response.json()["results"][0]
    assert saved["status"] == "saved"
    # Client paths never reach the disk; only the preserved display name keeps the basename.
    docs = flow.tmp / str(flow.user_id) / "games" / "Flow" / "docs"
    assert [p.name for p in docs.iterdir()] == [saved["filename"]]
    assert sorted(p for p in flow.tmp.rglob("*.docx")) == [docs / saved["filename"]]
    [item] = (await flow.client.get(f"{flow.game}/files/doc")).json()["files"]
    assert item["original_filename"] == "Manual.docx"

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    try:
        assert (await flow.client.get(f"{flow.game}/files/doc")).status_code == 404
        assert (await flow.client.get(item["url"])).status_code == 404
        refused = await flow.client.post(
            f"{flow.game}/files/doc", files={"file": ("x.docx", _docx(), DOCX)}
        )
        assert refused.status_code == 404
    finally:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=flow.user_id)
    assert [p.name for p in docs.iterdir()] == [saved["filename"]]


@pytest.fixture(name="saved_limits")
async def saved_limits_fixture():
    """Administration -> Limits overrides on the deployment row, restored afterwards."""
    limits = {"max_upload_size_mb": 1, "max_doc_size_mb": 2, "max_world_save_size_mb": 1}
    async with SessionLocal() as db:
        row = await get_or_create_app_integration_settings(db)
        before = {name: getattr(row, name, None) for name in limits}
        for name, value in limits.items():
            setattr(row, name, value)
        await db.commit()
    yield limits
    async with SessionLocal() as db:
        row = await get_or_create_app_integration_settings(db)
        for name, value in before.items():
            setattr(row, name, value)
        await db.commit()


@pytest.mark.usefixtures("saved_limits")
async def test_docs_follow_the_documents_cap_rather_than_the_image_cap(flow) -> None:
    # A 1.5 MB illustrated Word file: over the 1 MB image cap, within the 2 MB documents cap.
    illustrated = _docx(padding=MB + MB // 2)
    oversized = _docx(padding=2 * MB)
    response = await flow.client.post(
        f"{flow.game}/files/doc",
        files=[
            ("files", ("Illustrated manual.docx", illustrated, DOCX)),
            ("files", ("Scanned manual.docx", oversized, DOCX)),
            ("files", ("notes.txt", b"small", "text/plain")),
        ],
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["status"] == "saved" and results[0]["size"] == len(illustrated)
    assert results[1] == {
        "filename": "Scanned manual.docx",
        "status": "rejected",
        "reason": "Larger than 2 MB.",
    }
    assert results[2]["status"] == "saved"
    listed = (await flow.client.get(f"{flow.game}/files/doc")).json()["files"]
    assert sorted(f["original_filename"] for f in listed) == [
        "Illustrated manual.docx",
        "notes.txt",
    ]
    # The refused upload left nothing behind on disk.
    stored = sorted(
        p.name for p in (flow.tmp / str(flow.user_id) / "games" / "Flow" / "docs").iterdir()
    )
    assert stored == sorted(f["filename"] for f in listed)

    # Modpacks keep following the world-save cap.
    modpack = await flow.client.post(
        f"{flow.game}/files/modpack",
        files={"file": ("pack.zip", illustrated, "application/zip")},
    )
    assert modpack.json()["results"][0]["reason"] == "Larger than 1 MB."
