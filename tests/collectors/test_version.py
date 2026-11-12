"""VERSION at the repository root is where the version is written. The dashboard and the collector both read it, so
"Collector: up to date" compares two copies of the same value; the server compose file repeats it in its image tags,
and a test keeps the two equal."""
import re
from pathlib import Path

import yaml

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


def test_the_server_stack_runs_the_published_images_of_this_version():
    """docker-compose.server.yml pulls Infradeck's images by the version in VERSION, so a checkout of a release tag
    runs that release; deploy/build.yml builds every one of them from source instead."""
    root = VERSION_FILE.parent
    version = VERSION_FILE.read_text().strip()
    services = yaml.safe_load((root / "docker-compose.server.yml").read_text())["services"]
    built = yaml.safe_load((root / "deploy/build.yml").read_text())["services"]

    ours = {name: service["image"] for name, service in services.items() if service["image"].startswith("ghcr.io/")}
    assert set(ours) == {"backend", "agent-worker", "collector", "docker-proxy", "system-helper", "port-owners", "frontend"}
    for name, image in ours.items():
        assert re.fullmatch(rf"ghcr\.io/karloks2005/infradeck/[a-z-]+:{re.escape(version)}", image), (name, image)
        assert "build" not in services[name], name
    assert set(built) == set(ours) and all("build" in service for service in built.values())
