import pytest
from lydarr.file_manager import MediaEntry, MediaState, read_entries, _write


def test_read_entries_empty_file(tmp_path):
    f = tmp_path / "media.toml"
    f.write_text("", encoding = "utf-8")
    entries = read_entries(str(f))
    assert entries == []


def test_read_entries_missing_file(tmp_path):
    entries = read_entries(str(tmp_path / "nonexistent.toml"))
    assert entries == []


def test_read_entries_anime(tmp_path):
    f = tmp_path / "media.toml"
    f.write_text(
        '[[media]]\ntitle = "Solo Leveling"\ntype = "anime"\nsubmitters = ["SubsPlease", "Erai-raws"]\n',
        encoding = "utf-8",
    )
    entries = read_entries(str(f))
    assert len(entries) == 1
    assert entries[0].title == "Solo Leveling"
    assert entries[0].media_type == "anime"
    assert entries[0].submitters == ["SubsPlease", "Erai-raws"]
    assert entries[0].last_chapter == 0


def test_read_entries_manga(tmp_path):
    f = tmp_path / "media.toml"
    f.write_text(
        '[[media]]\ntitle = "One Piece"\ntype = "manga"\nsubmitters = []\nlast_chapter = 1100\n',
        encoding = "utf-8",
    )
    entries = read_entries(str(f))
    assert len(entries) == 1
    assert entries[0].media_type == "manga"
    assert entries[0].last_chapter == 1100


def test_read_entries_multiple(tmp_path):
    f = tmp_path / "media.toml"
    f.write_text(
        '[[media]]\ntitle = "Anime A"\ntype = "anime"\nsubmitters = []\n\n'
        '[[media]]\ntitle = "Manga B"\ntype = "manga"\nsubmitters = []\nlast_chapter = 50\n',
        encoding = "utf-8",
    )
    entries = read_entries(str(f))
    assert len(entries) == 2
    assert entries[0].title == "Anime A"
    assert entries[1].title == "Manga B"


def test_read_entries_search_name(tmp_path):
    f = tmp_path / "media.toml"
    f.write_text(
        '[[media]]\ntitle = "Solo Leveling"\ntype = "anime"\nsubmitters = []\nsearch_name = "Solo Leveling S2"\n',
        encoding = "utf-8",
    )
    entries = read_entries(str(f))
    assert entries[0].search_name == "Solo Leveling S2"


def test_write_then_read_roundtrip(tmp_path):
    f = tmp_path / "media.toml"
    entries = [
        MediaEntry(title = "Solo Leveling", media_type = "anime", submitters = ["SubsPlease"]),
        MediaEntry(title = "One Piece", media_type = "manga", submitters = [], last_chapter = 1100),
    ]
    _write(str(f), entries)
    recovered = read_entries(str(f))
    assert len(recovered) == 2
    assert recovered[0].title == "Solo Leveling"
    assert recovered[0].submitters == ["SubsPlease"]
    assert recovered[1].title == "One Piece"
    assert recovered[1].last_chapter == 1100


def test_write_empty_list(tmp_path):
    f = tmp_path / "media.toml"
    _write(str(f), [])
    assert f.read_text() == ""


def test_write_with_search_name(tmp_path):
    f = tmp_path / "media.toml"
    entries = [
        MediaEntry(title = "Solo Leveling", media_type = "anime", submitters = [], search_name = "Solo Leveling S2"),
    ]
    _write(str(f), entries)
    content = f.read_text()
    assert "search_name" in content
    assert "Solo Leveling S2" in content


def test_write_with_download_dir_roundtrip(tmp_path):
    f = tmp_path / "media.toml"
    entries = [
        MediaEntry(title = "Solo Leveling", media_type = "anime", submitters = [], download_dir = "/media/anime"),
        MediaEntry(title = "Dan Da Dan", media_type = "anime", submitters = []),
    ]
    _write(str(f), entries)
    content = f.read_text()
    assert 'download_dir = "/media/anime"' in content
    recovered = read_entries(str(f))
    assert recovered[0].download_dir == "/media/anime"
    assert recovered[1].download_dir == ""


def test_media_state_get_found():
    entries = [MediaEntry(title = "Solo Leveling"), MediaEntry(title = "Dan Da Dan")]
    state = MediaState(entries)
    result = state.get("Solo Leveling")
    assert result is not None
    assert result.title == "Solo Leveling"


def test_media_state_get_not_found():
    state = MediaState([MediaEntry(title = "Solo Leveling")])
    assert state.get("Nonexistent") is None


def test_media_state_entries():
    entries = [MediaEntry(title = "Solo Leveling"), MediaEntry(title = "Dan Da Dan")]
    state = MediaState(entries)
    result = state.entries()
    assert len(result) == 2


def test_media_state_titles():
    entries = [MediaEntry(title = "Solo Leveling"), MediaEntry(title = "Dan Da Dan")]
    state = MediaState(entries)
    assert state.titles() == ["Solo Leveling", "Dan Da Dan"]


@pytest.mark.asyncio
async def test_media_state_add(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([])
    await state.add(str(f), MediaEntry(title = "Solo Leveling", media_type = "anime"))
    assert "Solo Leveling" in state.titles()
    recovered = read_entries(str(f))
    assert any(e.title == "Solo Leveling" for e in recovered)


@pytest.mark.asyncio
async def test_media_state_add_duplicate_ignored(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([MediaEntry(title = "Solo Leveling")])
    await state.add(str(f), MediaEntry(title = "Solo Leveling"))
    assert state.titles().count("Solo Leveling") == 1


@pytest.mark.asyncio
async def test_media_state_remove(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([MediaEntry(title = "Solo Leveling"), MediaEntry(title = "Dan Da Dan")])
    await state.remove(str(f), "Solo Leveling")
    assert "Solo Leveling" not in state.titles()
    assert "Dan Da Dan" in state.titles()


@pytest.mark.asyncio
async def test_media_state_remove_nonexistent(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([MediaEntry(title = "Solo Leveling")])
    await state.remove(str(f), "Nonexistent")
    assert "Solo Leveling" in state.titles()


@pytest.mark.asyncio
async def test_media_state_update_entry(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([MediaEntry(title = "Solo Leveling", submitters = [])])
    await state.update_entry(str(f), "Solo Leveling", ["SubsPlease"], "Solo Leveling S2", "/media/anime")
    entries = state.entries()
    e = next(e for e in entries if e.title == "Solo Leveling")
    assert e.submitters == ["SubsPlease"]
    assert e.search_name == "Solo Leveling S2"
    assert e.download_dir == "/media/anime"


@pytest.mark.asyncio
async def test_media_state_update_last_chapter(tmp_path):
    f = tmp_path / "media.toml"
    state = MediaState([MediaEntry(title = "One Piece", media_type = "manga", last_chapter = 1100)])
    await state.update_last_chapter(str(f), "One Piece", 1101)
    entries = state.entries()
    e = next(e for e in entries if e.title == "One Piece")
    assert e.last_chapter == 1101


