"""VERSION at the repository root is the only place the version is written. The dashboard and the collector both
read it, so "Collector: up to date" compares two copies of the same value."""
import re
from pathlib import Path

from src.backend import config
from src.collector import collector

VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def test_the_dashboard_and_the_collector_report_the_version_in_the_version_file():
    version = VERSION_FILE.read_text().strip()

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert config.APP_VERSION == collector.COLLECTOR_VERSION == version


def test_the_collector_image_reads_the_version_file_next_to_it(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("9.8.7\n")
    monkeypatch.setattr(collector, "__file__", str(tmp_path / "collector.py"))

    assert collector._read_version() == "9.8.7"


def test_a_collector_without_a_version_file_says_dev(tmp_path, monkeypatch):
    folder = tmp_path / "a" / "b"
    folder.mkdir(parents=True)
    monkeypatch.setattr(collector, "__file__", str(folder / "collector.py"))

    assert collector._read_version() == "dev"
