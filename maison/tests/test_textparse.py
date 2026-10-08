from app.textparse import norm, city_key, tri_garage, tri_garden, tri_bathtub, detect_type, detect_works


def test_norm_handles_oe_and_accents():
    assert norm("Marcq-en-Barœul") == "marcq-en-baroeul"


def test_city_key_variants():
    assert city_key("St André lès Lille") == city_key("Saint-André-lez-Lille")
    assert city_key("Lille", "59160") == "lomme"
    assert city_key("Lille (Lomme)") == "lomme"
    assert city_key("MARCQ EN BAROEUL") == city_key("Marcq-en-Barœul")


def test_garage():
    assert tri_garage("Beau garage double") is True
    assert tri_garage("Maison sans garage") is False
    assert tri_garage("Belle maison") is None


def test_garden():
    assert tri_garden("jardin arboré de 400 m2") is True
    assert tri_garden("pas de jardin") is False


def test_bathtub():
    assert tri_bathtub("salle de bains avec baignoire") is True
    assert tri_bathtub("douche italienne") is None


def test_type():
    assert detect_type("Maison semi-individuelle") == "semi"
    assert detect_type("Maison individuelle non mitoyenne") == "detached"
    assert detect_type("Maison mitoyenne type 1930") == "terraced"
    assert detect_type("Maison") == "unknown"


def test_works():
    assert detect_works("Travaux à prévoir") == "heavy"
    assert detect_works("à rafraîchir") == "light"
    assert detect_works("Aucun travaux, rénovée en 2022") == "none"
    assert detect_works("Belle maison") is None
