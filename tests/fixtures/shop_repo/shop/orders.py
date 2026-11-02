import uuid

from shop.cart import Cart
from shop.storage import JsonStorage


def place_order(cart: Cart, storage: JsonStorage) -> str:
    if not cart.items:
        raise ValueError("cannot place an empty order")
    order_id = uuid.uuid4().hex
    storage.save(order_id, {"items": cart.items, "total_cents": cart.total_cents()})
    return order_id
