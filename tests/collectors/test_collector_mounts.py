import os

from src.collector import collector

MOUNTS = """\
/dev/sda1 / ext4 rw 0 0
proc /proc proc rw 0 0
tmpfs /run tmpfs rw 0 0
/dev/sda1 /var/snap/firefox/common ext4 rw 0 0
/dev/sdb1 /mnt/my\\040data ext4 rw 0 0
/dev/loop0 /snap/core/1 squashfs ro 0 0
overlay /var/lib/docker/overlay2/x/merged overlay rw 0 0
nas:/export /mnt/nfs nfs4 rw 0 0
/dev/sdc1 /mnt/later ext4 rw 0 0
"""


class FakeStat:
    def __init__(self, total_blocks):
        self.f_frsize = 1024**3 // 1000
        self.f_blocks = total_blocks
        self.f_bfree = total_blocks // 2
        self.f_bavail = total_blocks // 2
        self.f_files = 100
        self.f_ffree = 75


def test_collect_mounts_keeps_real_filesystems_once(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "mounts").write_text(MOUNTS)
    monkeypatch.setattr(collector, "HOST_PROC", proc)
    root = tmp_path / "root"
    monkeypatch.setattr(collector, "HOST_ROOT", root)
    sizes = {".": 1000, "var/snap/firefox/common": 1000, "mnt/my data": 4000, "mnt/nfs": 8000}
    monkeypatch.setattr(os, "statvfs", lambda path: FakeStat(sizes[os.path.relpath(path, root)]))
    # /mnt/later was mounted after the collector started, so it's only a directory in the container.
    monkeypatch.setattr(os.path, "ismount", lambda path: not str(path).endswith("later"))

    mounts = collector.collect_mounts()

    assert [(m["mount"], m["fs_type"]) for m in mounts] == [("/", "ext4"), ("/mnt/my data", "ext4"), ("/mnt/nfs", "nfs4")]
    assert mounts[0]["used_pct"] == 50.0
    assert mounts[0]["inodes_pct"] == 25.0
