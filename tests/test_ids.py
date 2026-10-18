from fantasy.data.ids import canonical_id, slugify


def test_slugify_examples():
    assert slugify("Nikola Jokić") == "nikola-jokic"
    assert slugify("Jaren Jackson Jr.") == "jaren-jackson-jr"
    assert slugify("De'Aaron Fox") == "deaaron-fox"
    assert slugify("Shai Gilgeous-Alexander") == "shai-gilgeous-alexander"
    assert slugify("Alperen Şengün") == "alperen-sengun"
    assert slugify("Kristaps Porziņģis") == "kristaps-porzingis"
    assert slugify("Dāvis Bertāns") == "davis-bertans"
    assert slugify("  P.J. Washington ") == "pj-washington"
    assert slugify("Trey Murphy III") == "trey-murphy-iii"


def test_slugify_is_ascii_and_idempotent():
    for n in ["Luka Dončić", "Jusuf Nurkić", "Nikola Vučević", "Bogdan Bogdanović", "Moussa Diabaté"]:
        s = slugify(n)
        assert s.isascii() and s == s.lower() and " " not in s
        assert slugify(s) == s


def test_canonical_aliases():
    assert canonical_id("Nicolas Claxton") == "nic-claxton"
    assert canonical_id("Jaren Jackson") == "jaren-jackson-jr"
