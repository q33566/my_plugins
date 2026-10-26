import httpx
import pytest

from gdrive_reader.drive import DriveClient
from gdrive_reader.reader import DriveReader
from tests.fakes import FOLDER, FakeDrive


def make_reader(drive, root="ROOT"):
    async def token():
        return "good-token"
    client = DriveClient(httpx.AsyncClient(transport=drive.transport(), base_url="https://www.googleapis.com"), token)
    return DriveReader(client, root)


async def test_lists_files_in_folder_and_subfolders_with_paths():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.add("p1", "report.pdf", "application/pdf", "ROOT", b"%PDF-1.4")
    d.add("sub", "Images", FOLDER, "ROOT")
    d.add("i1", "cat.png", "image/png", "sub", b"\x89PNG")
    listing = await make_reader(d).list_folder()
    paths = sorted(e["path"] for e in listing["files"])
    assert paths == ["Images/", "Images/cat.png", "report.pdf"]
    pdf = next(e for e in listing["files"] if e["path"] == "report.pdf")
    assert pdf["id"] == "p1" and pdf["mimeType"] == "application/pdf" and pdf["size"] == 8
    assert listing["folder"] == {"id": "ROOT", "name": "Test folder"}


async def test_follows_pagination():
    d = FakeDrive()
    d.page_size_cap = 2
    d.add("ROOT", "Test folder", FOLDER)
    for i in range(5):
        d.add(f"f{i}", f"note{i}.txt", "text/plain", "ROOT", b"x")
    listing = await make_reader(d).list_folder()
    assert [e["name"] for e in listing["files"]] == [f"note{i}.txt" for i in range(5)]


async def test_shortcut_shows_its_target_and_folder_shortcut_cycle_terminates():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.add("out", "elsewhere.txt", "text/plain", None, b"hi")
    d.add("s1", "link to elsewhere", "application/vnd.google-apps.shortcut", "ROOT",
          shortcutDetails={"targetId": "out", "targetMimeType": "text/plain"})
    d.add("s2", "loop back to root", "application/vnd.google-apps.shortcut", "ROOT",
          shortcutDetails={"targetId": "ROOT", "targetMimeType": FOLDER})
    listing = await make_reader(d).list_folder()
    by_name = {e["name"]: e for e in listing["files"]}
    assert by_name["link to elsewhere"]["id"] == "out"
    assert by_name["link to elsewhere"]["mimeType"] == "text/plain"
    assert by_name["link to elsewhere"]["shortcut"] is True
    assert len(listing["files"]) == 2


async def test_stops_at_item_limit_and_says_so():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    for i in range(30):
        d.add(f"f{i:02}", f"n{i:02}.txt", "text/plain", "ROOT", b"x")
    reader = make_reader(d)
    reader.max_items = 10
    listing = await reader.list_folder()
    assert len(listing["files"]) == 10
    assert listing["truncated"] is True
