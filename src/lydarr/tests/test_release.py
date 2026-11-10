import pytest

from lydarr.release import normalize, parse_release, split_season

# (name, group, title, season, episode, alt_episodes, resolution, hevc, batch)
CORPUS = [
    (
        "[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04 [1080p CR WEBRip HEVC AAC][MultiSub][74CA08B6]",
        "Erai-raws", "Kusuriya no Hitorigoto", 3, 4, (), 1080, True, False,
    ),
    (
        "[Erai-raws] Kusuriya no Hitorigoto 2nd Season - 04 [480p CR WEB-DL AVC AAC][MultiSub][7BF37BCC]",
        "Erai-raws", "Kusuriya no Hitorigoto", 2, 4, (), 480, False, False,
    ),
    (
        "[Half-Baked] The Apothecary Diaries - S03E04 [WEB 1080p HEVC AAC] | Kusuriya no Hitorigoto - 52 | The Apo",
        "Half-Baked", "The Apothecary Diaries", 3, 4, (52,), 1080, True, False,
    ),
    (
        "[Half-Baked] The Apothecary Diaries Season 2 - 04 [WEB 1080p HEVC E-AC3] | Kusuriya no Hitorigoto - 28",
        "Half-Baked", "The Apothecary Diaries", 2, 4, (28,), 1080, True, False,
    ),
    (
        "[Cytox] The Apothecary Diaries S03E04 1080p CR WEBRip Dual-Audio DDP 2.0 x265 | Kusuriya no Hitorigoto 3rd Season",
        "Cytox", "The Apothecary Diaries", 3, 4, (), 1080, True, False,
    ),
    (
        "[DKB] Kusuriya no Hitorigoto - S03E06 [1080p][HEVC x265 10bit][Multi-Subs][weekly]",
        "DKB", "Kusuriya no Hitorigoto", 3, 6, (), 1080, True, False,
    ),
    (
        "The Apothecary Diaries S03E03 Let Them Eat Cake 1080p CR WEB-DL MULTi AAC2.0 H.264-VARYG (Kusuriya no Hitorigoto)",
        "VARYG", "The Apothecary Diaries", 3, 3, (), 1080, False, False,
    ),
    (
        "[Trix] Kusuriya no Hitorigoto S03E06 [WEBRip 1080p AV1 Opus] (Multi Subs, VOSTFR) | The Apothecary Diaries",
        "Trix", "Kusuriya no Hitorigoto", 3, 6, (), 1080, False, False,
    ),
    (
        "[Judas] Kusuriya no Hitorigoto (The Apothecary Diaries) - S03E06 [1080p][HEVC x265 10bit][Multi-Subs] (Weekly)",
        "Judas", "Kusuriya no Hitorigoto", 3, 6, (), 1080, True, False,
    ),
    (
        "[ASW] Kusuriya no Hitorigoto - 54 [1080p HEVC x265 10Bit][AAC]",
        "ASW", "Kusuriya no Hitorigoto", None, 54, (), 1080, True, False,
    ),
    (
        "[SubsPlease] Sousou no Frieren - 28 (1080p) [ABCD1234].mkv",
        "SubsPlease", "Sousou no Frieren", None, 28, (), 1080, False, False,
    ),
    (
        "[EMBER] Tensei Shitara Ken Deshita S2 - 04 [1080p] [HEVC WEBRip]",
        "EMBER", "Tensei Shitara Ken Deshita", 2, 4, (), 1080, True, False,
    ),
    (
        "[ToonsHub] Firefly Wedding S01E05 1080p AMZN WEB-DL DDP2.0 H.264 (Hotaru no Yomeiri, Multi-Subs)",
        "ToonsHub", "Firefly Wedding", 1, 5, (), 1080, False, False,
    ),
    (
        "Fool.Night.S01E07.1080p.NF.WEB-DL.AAC2.0.H.264-VARYG",
        "VARYG", "Fool Night", 1, 7, (), 1080, False, False,
    ),
    (
        "[Erai-raws] JoJo no Kimyou na Bouken - Steel Ball Run - 03 [1080p NF WEB-DL AVC EAC3][MultiSub]",
        "Erai-raws", "JoJo no Kimyou na Bouken - Steel Ball Run", None, 3, (), 1080, False, False,
    ),
    (
        "[SubsPlease] Mob Psycho 100 III - 04 (720p) [0A1B2C3D].mkv",
        "SubsPlease", "Mob Psycho 100", 3, 4, (), 720, False, False,
    ),
    (
        "[SubsPlease] 86 - Eighty Six - 11 (1080p) [AAAAAAAA].mkv",
        "SubsPlease", "86 - Eighty Six", None, 11, (), 1080, False, False,
    ),
    (
        "[SubsPlease] Kaijuu 8-gou - 05 (1080p) [AAAAAAAA].mkv",
        "SubsPlease", "Kaijuu 8-gou", None, 5, (), 1080, False, False,
    ),
    (
        "[Judas] Black Clover (Season 1) [1080p][HEVC x265 10bit][Multi-Subs] (Batch)",
        "Judas", "Black Clover", 1, None, (), 1080, True, True,
    ),
    (
        "[SubsPlease] Black Clover (001-170) (1080p) [Batch]",
        "SubsPlease", "Black Clover", None, None, (), 1080, False, True,
    ),
    (
        "[Erai-raws] Black Clover - 01 ~ 12 [1080p][Multiple Subtitle]",
        "Erai-raws", "Black Clover", None, 1, (), 1080, False, True,
    ),
    (
        "[Anime Time] Black Clover S01E01-E13 [1080p][HEVC 10bit x265][AAC][Multi Sub]",
        "Anime Time", "Black Clover", 1, 1, (), 1080, True, True,
    ),
]


@pytest.mark.parametrize("name,group,title,season,episode,alts,resolution,hevc,batch", CORPUS)
def test_corpus(name, group, title, season, episode, alts, resolution, hevc, batch):
    r = parse_release(name)
    assert r.group == group
    assert r.title == title
    assert r.season == season
    assert r.episode == episode
    assert r.alt_episodes == alts
    assert r.resolution == resolution
    assert r.hevc is hevc
    assert r.batch is batch


def test_alias_titles_are_collected():
    r = parse_release(
        "[Half-Baked] The Apothecary Diaries - S03E04 [WEB 1080p HEVC AAC] | Kusuriya no Hitorigoto - 52"
    )
    assert r.titles == ("The Apothecary Diaries", "Kusuriya no Hitorigoto")

    r = parse_release("[Judas] Kusuriya no Hitorigoto (The Apothecary Diaries) - S03E06 [1080p]")
    assert r.titles == ("Kusuriya no Hitorigoto", "The Apothecary Diaries")


def test_version_suffix():
    assert parse_release("[SubsPlease] Show - 04v2 (1080p) [AAAAAAAA].mkv").version == 2
    assert parse_release("[Group] Show - S01E04v3 [1080p]").version == 3
    assert parse_release("[SubsPlease] Show - 04 (1080p)").version == 1


@pytest.mark.parametrize("name", [
    "[SubsPlease] Show - 04.5 (1080p) [AAAAAAAA].mkv",
    "[Group] Show - NCED [BD 1080p]",
    "[Group] Show - OVA 2 [1080p]",
    "[Group] Show Movie - 01 [1080p]",
    "[Group] Show Recap - 01 [1080p]",
])
def test_specials_are_flagged(name):
    assert parse_release(name).special is True


def test_episode_titles_do_not_flag_specials():
    r = parse_release("Show S01E05 The Special Movie Night 1080p WEB H.264-GRP")
    assert r.special is False
    assert r.episode == 5


def test_year_and_resolution_are_not_episodes():
    r = parse_release("[Group] Show (2024) - 07 [1920x1080]")
    assert r.episode == 7
    assert r.resolution == 1080

    r = parse_release("[Group] Show 2024 [1080p]")
    assert r.episode is None


def test_bare_number_fallback():
    assert parse_release("[Group] Show 07 [1080p]").episode == 7
    assert parse_release("[Group] Show Season 2 [1080p]").episode is None
    assert parse_release("[Group] Show Part 2 - 03 [1080p]").episode == 3


def test_4k_and_missing_resolution():
    assert parse_release("[Group] Show - 01 [4K HDR]").resolution == 2160
    assert parse_release("[Group] Show - 01").resolution is None


def test_tags_keep_every_bracket_block():
    r = parse_release("Show - 04 [1080p] [ToonsHub]")
    assert "toonshub" in r.tags


def test_unparseable_names_do_not_raise():
    for name in ("", "   ", "[]", "[Group]", "(((", "- 04", "S01E01"):
        parse_release(name)


@pytest.mark.parametrize("title,expected", [
    ("Kusuriya no Hitorigoto 3rd Season", ("Kusuriya no Hitorigoto", 3)),
    ("The Apothecary Diaries Season 3", ("The Apothecary Diaries", 3)),
    ("Tensei Shitara Ken Deshita S2", ("Tensei Shitara Ken Deshita", 2)),
    ("The Iceblade Sorcerer Shall Rule the World II", ("The Iceblade Sorcerer Shall Rule the World", 2)),
    ("Frieren: Beyond Journey's End", ("Frieren: Beyond Journey's End", None)),
    ("Show Part 2", ("Show Part 2", None)),
])
def test_split_season(title, expected):
    assert split_season(title) == expected


def test_normalize():
    assert normalize("Re:ZERO -Starting Life in Another World-") == "re zero starting life in another world"
    assert normalize("Pokémon  (2023)") == "pokemon 2023"
    assert normalize("") == ""
