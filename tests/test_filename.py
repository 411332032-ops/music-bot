"""storage.py 的照片部分：檔名清洗（防護 3）、撞名、真實格式判斷（architecture.md 第 9 節）。"""

from datetime import datetime
from pathlib import Path

import pytest

from music_bot import storage
from music_bot.storage import detect_image_ext, sanitize_filename_part, save_photo

WHEN = datetime(2026, 10, 21, 14, 3, 22)
FORBIDDEN = set('/\\:*?"<>|')


# ---------- 防護 3：sanitize_filename_part ----------


def test_plain_names_are_kept():
    assert sanitize_filename_part("ドライフラワー") == "ドライフラワー"
    assert sanitize_filename_part("Abbey Road") == "Abbey Road"
    assert sanitize_filename_part("周杰倫 🎵 七里香") == "周杰倫 🎵 七里香"


def test_windows_forbidden_characters_become_underscore():
    out = sanitize_filename_part('A/B\\C:D*E?F"G<H>I|J')
    assert not (set(out) & FORBIDDEN)
    assert out == "A_B_C_D_E_F_G_H_I_J"


@pytest.mark.parametrize(
    "evil", ["..\\..\\evil", "../../etc/passwd", "a/../../b", "..", "...", "a..b"]
)
def test_path_traversal_is_neutralised(evil):
    out = sanitize_filename_part(evil)
    assert ".." not in out
    assert not (set(out) & FORBIDDEN)


def test_control_characters_are_removed():
    assert sanitize_filename_part("a\nb\tc\x00d\x1fe") == "abcde"


def test_leading_and_trailing_dots_and_spaces_are_stripped():
    assert sanitize_filename_part("  .hello. ") == "hello"


@pytest.mark.parametrize("name", ["CON", "con", "PRN", "AUX", "NUL", "COM1", "lpt9"])
def test_windows_reserved_names_get_prefixed(name):
    assert sanitize_filename_part(name) == "_" + name


def test_reserved_name_as_part_of_longer_name_is_fine():
    assert sanitize_filename_part("CONCERT") == "CONCERT"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t", ".", "...", "  ..  "])
def test_names_that_end_up_empty_become_placeholder(empty):
    assert sanitize_filename_part(empty) == "未命名"


def test_long_names_are_truncated_without_trailing_space():
    out = sanitize_filename_part("a" * 29 + " " + "b" * 50)
    assert len(out) <= 30
    assert not out.endswith(" ")


def test_guard_off_passes_name_through(monkeypatch):
    monkeypatch.setattr(storage, "ENABLE_GUARD_3", False)
    assert sanitize_filename_part("a:b/../c") == "a:b/../c"


# ---------- save_photo ----------


def test_saved_name_follows_the_pattern(tmp_path):
    path = save_photo(tmp_path / "photos", b"data", WHEN, "某專輯", ".jpg")
    assert path.name == "2026-10-21 14-03-22 某專輯.jpg"
    assert path.read_bytes() == b"data"
    assert ":" not in path.name


def test_photos_dir_is_created_when_missing(tmp_path):
    target = tmp_path / "deep" / "photos"
    save_photo(target, b"x", WHEN, "n", ".png")
    assert target.is_dir()


def test_same_second_same_name_gets_dash_two_then_three(tmp_path):
    d = tmp_path / "photos"
    a = save_photo(d, b"1", WHEN, "某專輯", ".jpg")
    b = save_photo(d, b"2", WHEN, "某專輯", ".jpg")
    c = save_photo(d, b"3", WHEN, "某專輯", ".jpg")
    assert [p.name for p in (a, b, c)] == [
        "2026-10-21 14-03-22 某專輯.jpg",
        "2026-10-21 14-03-22 某專輯-2.jpg",
        "2026-10-21 14-03-22 某專輯-3.jpg",
    ]
    assert (a.read_bytes(), b.read_bytes(), c.read_bytes()) == (b"1", b"2", b"3")


def test_existing_file_is_never_overwritten(tmp_path):
    d = tmp_path / "photos"
    first = save_photo(d, b"original", WHEN, "x", ".jpg")
    save_photo(d, b"new", WHEN, "x", ".jpg")
    assert first.read_bytes() == b"original"


@pytest.mark.parametrize(
    "evil", ["..\\..\\evil", "../../evil", "C:\\Windows\\x", "a/b/c", "..", "evil:*?"]
)
def test_malicious_names_never_escape_photos_dir(tmp_path, evil):
    d = tmp_path / "photos"
    path = save_photo(d, b"x", WHEN, evil, ".jpg")
    assert path.resolve().parent == d.resolve()
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == [path]


def test_unknown_extension_is_saved_as_bin(tmp_path):
    path = save_photo(tmp_path / "photos", b"x", WHEN, "n", ".exe")
    assert path.suffix == ".bin"


@pytest.mark.parametrize("ext", [".jpg", ".png", ".gif", ".webp"])
def test_known_extensions_are_kept(tmp_path, ext):
    assert save_photo(tmp_path / "photos", b"x", WHEN, "n", ext).suffix == ext


# ---------- detect_image_ext：看檔案內容，不信檔名 ----------


def test_detects_jpeg():
    assert detect_image_ext(b"\xff\xd8\xff\xe0" + b"0" * 20) == ".jpg"


def test_detects_png():
    assert detect_image_ext(b"\x89PNG\r\n\x1a\n" + b"0" * 20) == ".png"


@pytest.mark.parametrize("magic", [b"GIF87a", b"GIF89a"])
def test_detects_gif(magic):
    assert detect_image_ext(magic + b"0" * 20) == ".gif"


def test_detects_webp():
    assert detect_image_ext(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == ".webp"


@pytest.mark.parametrize("junk", [b"", b"hello world, not an image", b"RIFF\x00\x00\x00\x00WAVEfmt "])
def test_rejects_non_images(junk):
    assert detect_image_ext(junk) is None


def test_save_photo_returns_a_path_object(tmp_path):
    assert isinstance(save_photo(tmp_path / "p", b"x", WHEN, "n", ".jpg"), Path)
