"""Empreintes visuelles (spec listing-deduplication : reconnaissance d'une même photo, images unies)."""
import itertools

from app import dedupe
from tests.dedupe_helpers import crop, jpeg, scene, thumbnail, uniform, watermark


def h(img, q=90):
    ph, dh, _ = dedupe.image_hashes(jpeg(img, q))
    return ph, dh


def test_same_photo_survives_resize_recompress_watermark_and_light_crop():
    for seed in range(8):
        src = scene(seed, (1920, 1440))
        ref = h(src)
        assert dedupe.same_photo(ref, h(thumbnail(src), q=60)), seed       # vignette e-mail 500×370
        assert dedupe.same_photo(ref, h(src, q=35)), seed                   # recompression forte
        assert dedupe.same_photo(ref, h(watermark(src))), seed              # filigrane d'agence
        assert dedupe.same_photo(ref, h(crop(src, 0.03))), seed             # recadrage de 3 %


def test_different_scenes_do_not_match():
    hashes = [h(scene(seed)) for seed in range(100, 140)]
    assert not any(dedupe.same_photo(a, b) for a, b in itertools.combinations(hashes, 2))


def test_uniform_image_has_low_std_and_photo_does_not():
    assert dedupe.image_hashes(jpeg(uniform()))[2] < dedupe.UNIFORM_STD
    assert dedupe.image_hashes(jpeg(scene(1)))[2] >= dedupe.UNIFORM_STD


def test_signed_bigint_roundtrip_and_hamming():
    for u in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
        s = dedupe.to_db(u)
        assert -(1 << 63) <= s < (1 << 63) and dedupe.from_db(s) == u
    assert dedupe.ham(0, (1 << 64) - 1) == 64 and dedupe.ham(5, 5) == 0


def test_png_and_palette_images_are_hashed():
    import io
    buf = io.BytesIO()
    scene(3).convert("P").save(buf, "PNG")
    ph, dh, std = dedupe.image_hashes(buf.getvalue())
    assert dedupe.same_photo((ph, dh), h(scene(3)))
