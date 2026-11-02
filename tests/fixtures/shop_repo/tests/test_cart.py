from shop.cart import Cart


def test_bulk_discount_applies_at_threshold():
    cart = Cart()
    cart.add("A1", 10)
    assert cart.total_cents() == 12500 * 90 // 100
