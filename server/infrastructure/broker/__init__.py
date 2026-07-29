"""Infrastructure layer — the broker driver.

Owns: the NATS connection, the JetStream stream definition, and the JSON
encoding of a payload on the wire.
Must not own: which subjects exist or what a message means — the subject
vocabulary and the two delivery contracts are declared in
`server/domain/coordination/broker.py`, and this satisfies them.
"""
