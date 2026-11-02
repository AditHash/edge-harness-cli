from shop.catalog import lookup

BULK_THRESHOLD = 10
BULK_DISCOUNT_PERCENT = 10


class Cart:
    def __init__(self) -> None:
        self.items: dict[str, int] = {}

    def add(self, sku: str, quantity: int = 1) -> None:
        lookup(sku)
        self.items[sku] = self.items.get(sku, 0) + quantity

    def total_cents(self) -> int:
        total = 0
        for sku, quantity in self.items.items():
            line = lookup(sku).price_cents * quantity
            if quantity >= BULK_THRESHOLD:
                line = line * (100 - BULK_DISCOUNT_PERCENT) // 100
            total += line
        return total
