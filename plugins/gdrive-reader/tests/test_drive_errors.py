import httpx
import pytest

from gdrive_reader.drive import DriveClient, DriveError
from tests.fakes import FOLDER, FakeDrive


class Tokens:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    async def __call__(self, force_refresh=False):
        self.calls.append(force_refresh)
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def client(d, tokens):
    return DriveClient(httpx.AsyncClient(transport=d.transport(), base_url="https://www.googleapis.com"),
                       tokens, sleep=lambda s: _noop())


async def _noop():
    return None


async def test_rate_limit_and_server_errors_are_retried():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.fail_next = [429, 503]
    meta = await client(d, Tokens(["good-token"])).get("ROOT")
    assert meta["name"] == "Test folder"


async def test_retries_give_up_with_a_clear_message():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.fail_next = [503] * 10
    with pytest.raises(DriveError) as e:
        await client(d, Tokens(["good-token"])).get("ROOT")
    assert e.value.status == 503 and "try again" in e.value.message.lower()


async def test_expired_access_token_is_refreshed_once():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    tokens = Tokens(["stale-token", "good-token"])
    meta = await client(d, tokens).get("ROOT")
    assert meta["id"] == "ROOT" and tokens.calls == [False, True]


async def test_rejected_after_refresh_asks_to_sign_in_again():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    with pytest.raises(DriveError) as e:
        await client(d, Tokens(["stale-token"])).get("ROOT")
    assert e.value.status == 401 and "sign in again" in e.value.message


async def test_missing_file_and_no_access_messages_are_actionable():
    d = FakeDrive()
    with pytest.raises(DriveError) as e:
        await client(d, Tokens(["good-token"])).get("nope")
    assert e.value.status == 404 and "not found" in e.value.message.lower()
    assert "access" in e.value.message.lower()


async def test_rate_limit_reported_as_403_is_retried():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.fail_next = [(403, "userRateLimitExceeded"), (403, "rateLimitExceeded")]
    assert (await client(d, Tokens(["good-token"])).get("ROOT"))["id"] == "ROOT"


async def test_export_over_googles_size_limit_says_so():
    d = FakeDrive()
    d.add("ROOT", "Test folder", FOLDER)
    d.fail_next = [(403, "exportSizeLimitExceeded")]
    with pytest.raises(DriveError) as e:
        await client(d, Tokens(["good-token"])).get("ROOT")
    assert "10 MB" in e.value.message and "access" not in e.value.message.lower()
