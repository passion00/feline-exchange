from dataclasses import dataclass


@dataclass
class Position:
    instrument: str
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0

    def apply_fill(self, signed_quantity: float, price: float) -> float:
        old_quantity = self.quantity
        if old_quantity == 0 or old_quantity * signed_quantity > 0:
            total = old_quantity + signed_quantity
            self.average_price = ((abs(old_quantity) * self.average_price + abs(signed_quantity) * price) / abs(total)) if total else 0.0
            self.quantity = total
            return 0.0
        closed = min(abs(old_quantity), abs(signed_quantity))
        pnl = closed * (price - self.average_price) * (1 if old_quantity > 0 else -1)
        self.realized_pnl += pnl
        self.quantity = old_quantity + signed_quantity
        if self.quantity == 0:
            self.average_price = 0.0
        elif old_quantity * self.quantity < 0:
            self.average_price = price
        return pnl

