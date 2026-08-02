from gwlab.swift.generator import REALISTIC_BOOKS, generate_realistic


def test_books_cover_the_three_asset_classes():
    assert set(REALISTIC_BOOKS) == {"equity", "govvie", "etf"}


def test_realistic_is_deterministic():
    assert generate_realistic(500, seed=7) == generate_realistic(500, seed=7)


def test_realistic_fail_rate_is_calibrated_not_inflated():
    # aggregate clean share should be high (mostly-green), unlike the mechanism mix
    gen = generate_realistic(2000, seed=1)
    clean = sum(1 for g in gen if g.directive.outcome == "clean")
    assert clean / len(gen) > 0.85  # realistic aggregate, not the ~90%-fail teaching mix
