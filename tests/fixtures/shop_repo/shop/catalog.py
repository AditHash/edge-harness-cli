from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int


CATALOG = {
    "A1": Product("A1", "Widget", 1250),
    "B2": Product("B2", "Gadget", 899),
}


def lookup(sku: str) -> Product:
    try:
        return CATALOG[sku]
    except KeyError:
        raise KeyError(f"unknown sku {sku}") from None
