"""client — GUI delivery layer: Tk window, Pillow rendering, and the network client.

Depends only on the shared core engine package. Must not own: game rules,
simulation, or server transport concerns.

Sub-packages: `controllers` (the `IGameController` seam and its local/network
implementations), `network` (WebSocket transport and wire-frame decoding),
`notation` (algebraic square conversion), `auth` (pre-GUI CLI login), and `ui`
(Tk window, Pillow rendering, preferences).
"""
