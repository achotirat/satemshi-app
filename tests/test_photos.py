from __future__ import annotations

import os
from datetime import date, datetime

from conftest import TZ, jpeg_with_exif
from satemshi.config import PhotosConfig
from satemshi.photos import (
    PhotoStore,
    exif_datetime,
    suffix_for_content_type,
    taken_at,
)


def test_exif_datetime_reads_datetime_original():
    assert exif_datetime(jpeg_with_exif("2026:08:04 09:15:00")) == datetime(
        2026, 8, 4, 9, 15, 0
    )


def test_exif_datetime_ignores_a_file_without_exif():
    assert exif_datetime(b"\xff\xd8\xff\xd9") is None
    assert exif_datetime(b"not a jpeg") is None


def test_taken_at_falls_back_to_mtime(tmp_path):
    path = tmp_path / "screenshot.png"
    path.write_bytes(b"png")
    stamp = datetime(2026, 8, 4, 18, 0, tzinfo=TZ).timestamp()
    os.utime(path, (stamp, stamp))

    assert taken_at(path, TZ) == datetime(2026, 8, 4, 18, 0, tzinfo=TZ)


def test_taken_at_prefers_exif_over_mtime(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg_with_exif("2026:08:04 09:15:00"))
    stamp = datetime(2020, 1, 1, tzinfo=TZ).timestamp()
    os.utime(path, (stamp, stamp))

    assert taken_at(path, TZ) == datetime(2026, 8, 4, 9, 15, tzinfo=TZ)


def test_suffix_for_content_type():
    assert suffix_for_content_type("image/png") == ".png"
    assert suffix_for_content_type("image/jpeg; charset=binary") == ".jpg"
    assert suffix_for_content_type(None) == ".jpg"


def test_store_files_the_photo_by_its_own_date(vault):
    store = PhotoStore(vault, PhotosConfig(), TZ)
    when = datetime(2026, 8, 4, 9, 15, tzinfo=TZ)

    photo = store.store(b"bytes", when, "line-1", ".jpg")

    assert photo.vault_relative == "Attachments/2026/08/20260804-091500-line-1.jpg"
    assert (vault / photo.vault_relative).read_bytes() == b"bytes"


def test_find_for_day_spans_vault_and_source_dirs(vault, tmp_path):
    phone = tmp_path / "phone"
    (phone / "DCIM").mkdir(parents=True)
    today = phone / "DCIM" / "IMG_1.jpg"
    today.write_bytes(jpeg_with_exif("2026:08:04 09:15:00"))
    old = phone / "DCIM" / "IMG_0.jpg"
    old.write_bytes(jpeg_with_exif("2020:01:01 09:15:00"))
    (phone / "notes.txt").write_text("not a photo")

    store = PhotoStore(vault, PhotosConfig(source_dirs=(str(phone),)), TZ)
    stored = store.store(
        b"bytes", datetime(2026, 8, 4, 20, 0, tzinfo=TZ), "line-1", ".jpg"
    )
    # No EXIF in those bytes, so the sweep dates it by mtime.
    stamp = datetime(2026, 8, 4, 20, 0, tzinfo=TZ).timestamp()
    os.utime(stored.path, (stamp, stamp))

    found = store.find_for_day(date(2026, 8, 4))

    assert [photo.path.name for photo in found] == [
        "IMG_1.jpg",
        "20260804-200000-line-1.jpg",
    ]
    assert [photo.source for photo in found] == ["scan", "vault"]
    assert found[1].vault_relative.startswith("Attachments/")


def test_find_for_day_is_empty_when_nothing_matches(vault):
    store = PhotoStore(vault, PhotosConfig(), TZ)
    assert store.find_for_day(date(2026, 8, 4)) == []


def test_scanned_photos_are_recorded_by_absolute_path(vault, tmp_path, monkeypatch):
    phone = tmp_path / "phone"
    phone.mkdir()
    (phone / "IMG_5.jpg").write_bytes(jpeg_with_exif("2026:08:04 07:00:00"))
    monkeypatch.chdir(tmp_path)

    store = PhotoStore(vault, PhotosConfig(source_dirs=("./phone",)), TZ)
    (photo,) = store.find_for_day(date(2026, 8, 4))

    assert photo.path.is_absolute()
    assert photo.vault_relative is None
