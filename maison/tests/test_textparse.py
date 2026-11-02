from app.textparse import norm, city_key, tri_garage, tri_garden, tri_bathtub, detect_type, detect_works, type_fields


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
    assert detect_type("Maison semi-individuelle")[0] == "semi"
    assert detect_type("Maison individuelle non mitoyenne") == ("detached", "explicit", "non mitoyenne")
    assert detect_type("Maison mitoyenne type 1930") == ("terraced", "weak", "mitoyenne")
    assert detect_type("Maison") == ("unknown", None, None)


def test_type_independent_kitchen_is_not_a_detached_house():
    # « cuisine indépendante » faisait passer la maison en individuelle (43 cas sur 81 dans le spike)
    assert detect_type("Séjour lumineux, cuisine indépendante équipée, 3 chambres") == ("unknown", None, None)
    assert detect_type("Entrée indépendante, garage")[0] == "unknown"


def test_type_garage_link():
    assert detect_type("Maison mitoyenne par le garage, 3 chambres")[:2] == ("link_detached", "explicit")
    assert detect_type("Maisons jumelées par les garages")[0] == "link_detached"
    assert detect_type("Maison mitoyenne uniquement par des garages")[0] == "link_detached"
    assert detect_type("Maison accolée côté garage")[0] == "link_detached"
    assert detect_type("Maison mitoyenne d'un côté par le garage")[0] == "link_detached"
    # un garage cité à côté de « semi-mitoyenne » ne dit rien de la liaison
    assert detect_type("Maison semi-mitoyenne avec jardin et garage")[0] == "semi"


def test_type_sides():
    assert detect_type("Maison individuelle mitoyenne d'un seul côté") == ("semi", "explicit", "mitoyenne d'un seul cote")
    assert detect_type("mitoyenne d’un côté")[0] == "semi"            # apostrophe typographique
    assert detect_type("Maison jumelée")[0] == "semi"
    assert detect_type("Maison mitoyenne des deux côtés") == ("terraced", "explicit", "mitoyenne des deux cotes")
    assert detect_type("mitoyenne de chaque côté")[0] == "terraced"
    assert detect_type("Maisons de ville en bande")[:2] == ("terraced", "explicit")


def test_type_detached_and_weak_terms():
    assert detect_type("Maison 4 façades sur 600 m²") == ("detached", "explicit", "4 facades")
    assert detect_type("Maison individuelle de plain-pied") == ("detached", "explicit", "individuelle")
    assert detect_type("Aucune mitoyenneté, sans vis-à-vis")[0] == "detached"
    assert detect_type("Pavillon non mitoyen")[0] == "detached"
    # « individuelle mitoyenne » = maison unifamiliale accolée
    assert detect_type("Maison individuelle mitoyenne de 1930") == ("terraced", "weak", "mitoyenne")
    assert detect_type("Belle maison de ville") == ("terraced", "weak", "maison de ville")
    assert detect_type("Maison bel-étage")[:2] == ("terraced", "weak")
    assert detect_type("Villa des années 30")[0] == "unknown"        # aucune « villa » isolée dans le spike


def test_type_fields():
    assert type_fields("Maison semi-mitoyenne") == {"property_type": "semi", "property_type_src": "text_explicit"}
    assert type_fields("Maison de ville") == {"property_type": "terraced", "property_type_src": "text_weak"}
    assert type_fields("Maison") == {"property_type": "unknown", "property_type_src": None}


def test_works():
    assert detect_works("Travaux à prévoir") == "heavy"
    assert detect_works("à rafraîchir") == "light"
    assert detect_works("Aucun travaux, rénovée en 2022") == "none"
    assert detect_works("Belle maison") is None


def test_works_negations_and_typos():
    assert detect_works("Une opportunité rare, sans aucun travaaux à prévoir !") == "none"
    assert detect_works("Aucun travaux à prévoir, posez vos meubles") == "none"
    assert detect_works("Pas de travaux à prévoir") == "none"
    assert detect_works("Posez vos valises !") == "none"
    assert detect_works("Gros travaux à prévoir, pas de garage") == "heavy"
