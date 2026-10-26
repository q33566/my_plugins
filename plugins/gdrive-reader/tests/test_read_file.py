import pytest

from gdrive_reader.drive import DriveError
from tests.fakes import FOLDER, FakeDrive
from tests.pdfs import blank_pdf, text_pdf
from tests.test_list_folder import make_reader

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def drive_with(*files):
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    for f in files:
        d.add(*f[:4], **(f[4] if len(f) > 4 else {}))
    return d


async def test_plain_text_file_is_returned_as_text():
    d = drive_with(("t1", "notes.txt", "text/plain", "ROOT"))
    d.files["t1"]["_content"] = "héllo\nworld".encode()
    r = await make_reader(d).read_file("t1")
    assert r["kind"] == "text" and r["text"] == "héllo\nworld"
    assert r["file"]["name"] == "notes.txt"


async def test_google_doc_and_sheet_are_exported():
    d = drive_with(("doc", "Plan", "application/vnd.google-apps.document", "ROOT",
                    {"exports": {"text/plain": b"Doc body"}}),
                   ("sh", "Budget", "application/vnd.google-apps.spreadsheet", "ROOT",
                    {"exports": {"text/csv": b"a,b\n1,2\n"}}))
    reader = make_reader(d)
    assert (await reader.read_file("doc"))["text"] == "Doc body"
    sheet = await reader.read_file("sh")
    assert sheet["text"] == "a,b\n1,2\n" and "first sheet" in sheet["note"]


async def test_pdf_with_text_layer_returns_its_text():
    d = drive_with(("p1", "report.pdf", "application/pdf", "ROOT"))
    d.files["p1"]["_content"] = text_pdf("Hello Drive PDF")
    r = await make_reader(d).read_file("p1")
    assert r["kind"] == "text" and "Hello Drive PDF" in r["text"]


async def test_pdf_without_text_is_handed_over_as_a_file():
    d = drive_with(("p2", "scan.pdf", "application/pdf", "ROOT"))
    d.files["p2"]["_content"] = blank_pdf(2)
    r = await make_reader(d).read_file("p2")
    assert r["kind"] == "pdf_blob" and r["data"] == d.files["p2"]["_content"]
    assert r["pages"] == 2


async def test_image_is_returned_as_image():
    d = drive_with(("i1", "cat.png", "image/png", "ROOT"))
    d.files["i1"]["_content"] = PNG
    r = await make_reader(d).read_file("i1")
    assert r == {"kind": "image", "data": PNG, "mimeType": "image/png", "file": r["file"]}


async def test_unsupported_and_too_large_files_get_a_clear_message():
    d = drive_with(("z1", "archive.zip", "application/zip", "ROOT"),
                   ("big", "huge.png", "image/png", "ROOT"))
    d.files["big"]["size"] = str(500 * 1024 * 1024)
    reader = make_reader(d)
    z = await reader.read_file("z1")
    assert z["kind"] == "unsupported" and "application/zip" in z["message"]
    big = await reader.read_file("big")
    assert big["kind"] == "too_large" and "500.0 MB" in big["message"]


async def test_files_outside_the_folder_are_refused_and_urls_are_accepted():
    d = drive_with(("sub", "Sub", FOLDER, "ROOT"), ("t2", "deep.txt", "text/plain", "sub"))
    d.files["t2"]["_content"] = b"deep"
    d.add("secret", "private.txt", "text/plain", None, b"do not read")
    reader = make_reader(d)
    ok = await reader.read_file("https://drive.google.com/file/d/t2/view?usp=sharing")
    assert ok["text"] == "deep"
    with pytest.raises(DriveError) as e:
        await reader.read_file("secret")
    assert "outside the configured folder" in e.value.message and "Test folder" in e.value.message


async def test_shortcut_target_listed_in_the_folder_can_be_read():
    d = drive_with(("s1", "link", "application/vnd.google-apps.shortcut", "ROOT",
                    {"shortcutDetails": {"targetId": "out", "targetMimeType": "text/plain"}}))
    d.add("out", "elsewhere.txt", "text/plain", None, b"via shortcut")
    assert (await make_reader(d).read_file("out"))["text"] == "via shortcut"
