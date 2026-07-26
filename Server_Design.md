# Server Design — Scaling a Real-Time Chess Server (Scale Week, CTD)
 
This document reasons about scale architecture **not** based on the specific internal structure of the existing code, but based on the general pattern the server already follows: **a server that talks to clients over WebSocket, in a Pub/Sub model** — a client connects, "subscribes" to the event channels relevant to it (the room it's playing in, its personal notifications), and the server "publishes" events to them in real time (a move started, a piece was captured, the game ended). The rest of the architecture is built forward from this one pattern.
 
Before scaling, it's worth remembering where we started — a single server handling everything:
 
```
Clients
    │
Game Server
    │
Database
```
 
That's fine for tens or hundreds of users. It falls apart at 100 million registered users and 10 million concurrent players, because a single machine — no matter how strong — has finite memory, finite cores, finite bandwidth, and a single point of failure. So the goal stops being "write a good server" and becomes "design a **system of servers** that behaves like one giant server."
 
## Base model: Client ↔ WebSocket ↔ Pub/Sub
 
Before talking about scale, it's worth being precise about the logical model:
 
- **Connection** — every client opens one WebSocket against "the server" (in practice: against some particular gateway). It's a bidirectional, long-lived channel (stays open for the whole game session, not just a single request).
- **Publish** — every meaningful change in game state (a move started, two pieces collided, a piece was captured, the game ended) is an **event** published on a channel (topic) belonging to that room/game.
- **Subscribe** — every client in a given room (player or spectator) is, logically, a subscriber on that room's channel. The moment an event is published, it reaches all of its subscribers.
- This is **exactly** why Pub/Sub fits and plain request-response doesn't: a single event (a player's move) needs to reach several recipients (the opponent, spectators) without the publisher needing to know who they are.
The question that the rest of the design is built from: **what happens once "the server" is no longer a single process, but a fleet of Dockers?** Publish and subscribe still have to work even when the "publisher" (the logic computing the game) and the "subscriber" (the client's socket) sit on two completely different machines.
 
## Part 1: Docker / Kubernetes / K3s — how it fits together
 
Once we have a server, spinning up ten more shouldn't mean installing everything by hand ten times — that's what Docker is for. Docker is a "box" containing a small OS, our code, and every library the code needs. Build one image, and you can run as many copies of it as you want:
 
```
Image
   │
 ├── Docker 1
 ├── Docker 2
 ├── Docker 3
 ├── Docker 4
```
 
Every copy runs the exact same code — no matter which machine it runs on, the behavior is identical. This is **horizontal scaling**: instead of building one enormous server, it's far simpler to add more copies of the same one. If each Docker can handle ~20,000 users, then 200,000 users need about 10 Dockers, 2 million users need about 100, and 10 million users need hundreds.
 
- **Docker** gives a consistent deployment unit for each role (gateway, matchmaking, game-authority, persistence) — each one comes up identically everywhere, ready for replication.
- **Kubernetes / K3s** run many instances of each image, handle service discovery/load balancing, and drive autoscaling based on load. Kubernetes constantly watches CPU, memory, and connection counts per Docker, and reacts:
```
100 Docker
↓
all running at 95% CPU
↓
Kubernetes spins up more
↓
150 Docker
```
 
and just as automatically scales back down when load drops:
 
```
150 Docker
↓
all running at only 10% CPU
↓
40 Docker
```
 
K3s is Kubernetes shrunk down (single binary, fewer moving parts) — same model, lighter weight.
 
- The practical conclusion: **stateless** roles (gateway, matchmaking-API, persistence-writer) are a plain `Deployment` + HPA. The role that holds **the simulation in memory** (game-authority) is stateful and needs separate thought — it can't just get another replica without a mechanism that prevents two replicas from both "thinking" they own the same room at once (split-brain).
## Part 2: Answers to the design questions
 
### Question 1 — 100 million registered users: which DB?
 
**SQLite doesn't fit** — not mainly because of volume (a 100M-row table is something a server-based RDBMS absorbs easily), but because of:
 
1. **Single writer**: a single SQLite file allows one writer at a time. An ELO update at the end of a game, multiplied by millions of games finishing every second (see Question 4), immediately becomes a bottleneck.
2. **One machine, one disk**: no built-in replication, no sharding, no HA — the machine crashes, the whole DB crashes.
3. **Not built for multi-process/multi-machine access** — the moment there are many Dockers (gateway, game-authority, matchmaking...) all needing to touch the same user/rating data concurrently, that's exactly the case SQLite was never meant for. It's embedded, not client-server: any server process that wants to reach it needs direct access to the same file, which is simply impossible across hundreds of servers on different machines.
**What to use instead — split by data type, not one DB for everything:**
 
- **Users / auth / ELO** — needs real ACID (unique username, password, atomic rating update) → server-based RDBMS (PostgreSQL/MySQL) with a primary + read replicas. 100M rows still comfortably fits a single table; once write/connection **throughput** (not volume) becomes the bottleneck, move to sharding by `user_id` (Vitess/Citus, or a natively distributed DB like CockroachDB/YugabyteDB).
- **Presence / session mapping** (who's connected, to which gateway, to which channel) — Redis: low latency, doesn't need strong durability, and the Pub/Sub layer itself (Question 2) usually runs on the same technology family anyway.
- **Game/move history** — append-only, huge sustained write volume (see Question 4). At this scale, a wide-column store (Cassandra/ScyllaDB/DynamoDB) or object storage, not a single RDBMS.
- **Leaderboard** — not a live `ORDER BY` over 100M rows; a Redis Sorted Set updates incrementally instead.
### Question 2 — 10 million concurrent users: how does the Pub/Sub model spread across many servers?
 
A single server isn't enough (one process realistically caps out at tens of thousands of connections, and at one core's worth of CPU for real-time simulation). Once there are many Dockers, the core question becomes: **the publisher and subscriber of the same channel can sit on different machines — how does that still work?**
 
The answer: separate "who holds the client's socket" from "who computes the game state," and connect the two through a **shared broker** (Redis Pub/Sub, NATS, or Kafka) that acts as the backbone every Docker talks through:
 
```
Client
  ↓
Load Balancer
  ↓
Gateway 1   Gateway 2   Gateway 3   Gateway 4
```
 
1. **Gateway tier (stateless)** — each Docker here holds thousands of open client sockets. It computes nothing about the game itself; its job is to bridge: a message from a client (a move) → published to the broker on that room's channel; an event from the broker on a channel its clients are subscribed to → forwarded down the socket. Scales freely behind a load balancer, and can be geo-distributed. The load balancer's only job is spreading connections evenly, so no single gateway gets overloaded — player one lands on Gateway 2, player two on Gateway 4, and so on.
2. **Broker / Pub-Sub backbone** — the shared infrastructure that lets a publish on machine A reach a subscribe on machine B, without any gateway needing to know physically where anything lives. This is also the basis for "everyone can play with everyone, and anyone can join any room": a client doesn't need to "connect to the right server" — it connects to whichever gateway is geographically close, and that gateway subscribes to the right channel through the broker.
3. **Game-authority workers (stateful)** — this is where the real-time simulation actually runs. Each instance "owns" a set of rooms (a shard). It's the sole publisher on the channel of every room it holds, and the subscriber on the input (moves) channel for those same rooms. **Critical**: every room must have a **single owner** at any given moment — Pub/Sub solves fan-out (one message → many listeners), but it does not solve "who is allowed to write" (write authority). That needs a separate coordination layer (e.g. a lock/lease in Redis, consistent hashing by room-id, or a dedicated service like etcd) that guarantees two workers never both think they own the same room:
```
Room 450
  ↓
Server B
```
 
If Server B ever crashes, Kubernetes starts a replacement and the mapping is updated:
 
```
Room 450
  ↓
Server D
```
 
...and Server D takes over from there. Without a single owner, two servers could each accept the same move and disagree — one says legal, one says illegal — and now there are two different versions of the same game. That's exactly what a single-owner-per-room rule prevents.
 
4. **Matchmaking service** — itself a kind of global Pub/Sub channel (or shared queue): players "register" to search, and once a match is found, a "game-created" event is published that redirects both clients (through their gateways) to the newly created room channel. It has to be shared across every Docker, or a player who connects through Gateway A would never be matched with one who connects through Gateway B.
5. **Persistence workers (stateless, async)** — another kind of subscriber, this time on the "game-finished" channel — writing to the DB in batches without blocking the room's shutdown.
In short: Pub/Sub isn't just the protocol facing the client — it's also the pattern connecting every Docker on the server side. The one difference is that the server side also needs a **write-ownership** layer (who owns each channel), something a client never has to worry about since it's only ever a subscriber/publisher-of-input.
 
### Question 3 — network traffic: a move every 2 seconds
 
Because pieces in this concept move *over time* (there's a cooldown/travel duration) rather than jumping instantly, it is **not** viable to publish a full board snapshot on every render frame (potentially dozens of times per second) — that's simply impossible at this network scale. The right approach in a Pub/Sub model: publish only a **sparse event** at the start of each move (which piece, from where, to where, start time, duration), and let each client do client-side interpolation to render the motion smoothly without any additional traffic.
 
**Rough numbers:**
 
- 10,000,000 active players × one move per 2 seconds = **5,000,000 publish-events/second** coming in (client → gateway → broker).
- Each move message is a small JSON payload (type, room-id, from/to) — with WS framing/TLS, roughly ~150–250 bytes. 5M/sec × ~200B ≈ **~1GB/second ≈ ~8Gbps** inbound alone.
- Each event reaches the subscribers of that channel — at least the opponent, sometimes spectators too; fan-out ×2–3 on average → **~10–15M deliveries/second** outbound, another ~2–3GB/second. Total aggregate is roughly **20–30 Gbps**, before heartbeats (relatively small).
**Is that a lot or a little?** In aggregate, it's the scale of a large real-time infrastructure (comparable to major gaming/chat services) — not something a single datacenter absorbs comfortably. But the messages are small, and every channel (room) is completely independent of the others — meaning it **naturally shards** by channel across geographically distributed gateways/workers, each one absorbing only a small slice. This is why the sharding from Question 2 isn't just a CPU solution — it's a bandwidth solution too.
 
At the single-Docker level this collapses to almost nothing: if each Docker handles ~20,000 users, that's `20,000 ÷ 2 = 10,000 messages/second × ~100–200 bytes ≈ 1–2 MB/second` — trivial for one machine. The whole point of horizontal scaling here isn't "how much total traffic is there," it's "make sure no single Docker absorbs more than its share."
 
### Question 4 — average game lasts 30–90 seconds: what does that mean for the Dockers' roles?
 
- ~5,000,000 concurrent games (10M players ÷ 2), average lifetime ~60 seconds → roughly **~80,000 game-channels opening and closing every second**. That's enormous churn.
Using **Little's Law** (average number = arrival rate × average time in system):
 
```
10,000,000 players ÷ 2 players per game = 5,000,000 concurrent active games
 
5,000,000 games ÷ ~60 seconds average per game
≈ 83,000 games created (and finished) every second
```
 
- **A channel-per-container is out of the question**: the overhead of allocating/starting a Pod (even on K3s) is significantly larger than a 30–90 second channel lifetime. The unit of scale has to be **one long-lived worker hosting thousands of channels at once** (a game-authority instance), and scaling means adding more such instances — not another container per room.
- **Persistence can't be synchronous**: writing a finished game (summary row + moves + stats update) at ~80K/second would choke any single DB if it were a blocking write inside the channel-close path itself. It has to be decoupled: game-authority publishes a "finished" event to the broker, and persistence workers consume it in batches separately — the queue (broker) has to absorb this even if the DB is momentarily slow, or a backlog builds up.
- **Autoscaling differs by layer**: the game-authority fleet has to scale up/down **fast** (within seconds, since demand itself turns over roughly every ~60 seconds) based on active-channel count/CPU load. The DB layer grows slowly and steadily (cumulative write volume). The gateway layer scales by **open connections** — which stay open far longer than a single game, since a player plays several games in a row on the same socket. That's exactly why Gateway and Game-Authority are two components with completely different rates of change, and therefore need to be two separate Dockers with different scaling policies (see summary table below).
## How it all works together
 
```
             Clients
                 │
          Load Balancer
                 │
      ┌──────────┴──────────┐
      │                     │
   Gateway              Gateway
      │                     │
      └──────────┬──────────┘
                 │
          Broker (Pub/Sub)
                 │
      ┌──────────┼──────────┐
      │          │          │
 Game Server Game Server Game Server
      │          │          │
      └───────┬──┴──────────┘
              │
     Persistence Workers
              │
      PostgreSQL / Redis /
         Cassandra
```
 
The core idea behind the whole design is **separation of responsibility**: each component does exactly one job (accepting connections, running games, matching players, persisting data, and so on). That makes it possible to add more copies of any one component as needed, and lets the system grow from hundreds of users to tens or hundreds of millions without changing the game's core logic.
 
And the whole time, in the background, Kubernetes manages the entire system: adding Dockers under load, removing them as load drops, restarting any that crash, making sure enough servers are always available, and spreading load across them. So instead of relying on one giant server, the result is a system made of hundreds or thousands of instances of the same components, each handling only a small slice of the work, with Kubernetes making sure they all act as one unit. That's exactly what a scalable cloud system means: the number of active servers can grow or shrink with load, with almost no effect on users and no change to the server's own code.
 
## Summary — roles of the Dockers
 
| Role | State | Depends on Pub/Sub broker? | Scaling trigger |
|---|---|---|---|
| Gateway (WS termination) | Stateless | subscribe/publish bridge to the client | # connections |
| Matchmaking | Shared state (global queue/channel) | Yes — "game-created" channel | # waiting players |
| Game-authority | Stateful (holds simulation in memory) | Publisher on the room channel, subscriber on input | # active channels / CPU |
| Coordination (write-ownership) | Redis / etcd | Not itself, but supports it | — |
| Persistence writer | Stateless consumer | Subscriber on "finished" channel | Queue depth |
| DB — users/ELO | Stateful cluster | No | # users / write throughput |
| DB — game history | Stateful cluster | No | Write volume |
 
## Open questions for further thought
 
- What happens when two players from distant regions (say the US and Japan) play against each other — which region does the game-authority for their channel run in, and how does that affect latency for each side?
- Reconnect: a client that disconnects and comes back needs to find its channel again — that requires the user→room mapping to be reachable from every gateway, not just the one that held the original socket.
- Rolling deploy/graceful shutdown of game-authority: an instance holding thousands of live channels can't just be killed outright — it needs to drain (stop accepting new channels, let existing ones finish, then shut down).