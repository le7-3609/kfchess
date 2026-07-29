"""Domain sub-package — the contracts the fleet coordinates through.

Owns: the vocabulary of coordination — where a seat is, who owns a room, who
holds a lease — as ports plus the in-process implementations behind them.
Must not own: any driver, any wire format, or the policy that decides who
*should* own a room (that is the application's placement logic).

These live in the domain rather than beside their Redis drivers because the
inner layer is the one that needs them: `RoomManager` and the lease manager
depend on the contract, and infrastructure satisfies it from outside. Declaring
them here is what keeps the arrow pointing application <- infrastructure instead
of dragging a Redis import inward.
"""
