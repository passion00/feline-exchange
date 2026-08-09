from feline.core.events import OrderStatus

ALLOWED={
 OrderStatus.NEW:{OrderStatus.ACCEPTED,OrderStatus.REJECTED},
 OrderStatus.ACCEPTED:{OrderStatus.PARTIALLY_FILLED,OrderStatus.FILLED,OrderStatus.CANCELLED,OrderStatus.EXPIRED,OrderStatus.REJECTED},
 OrderStatus.PARTIALLY_FILLED:{OrderStatus.PARTIALLY_FILLED,OrderStatus.FILLED,OrderStatus.CANCELLED,OrderStatus.EXPIRED},
}
def validate_transition(old:OrderStatus,new:OrderStatus)->None:
 if new not in ALLOWED.get(old,set()):raise ValueError(f"invalid order transition: {old.value}->{new.value}")
