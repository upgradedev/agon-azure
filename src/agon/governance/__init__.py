"""Deterministic governance for Agon: claims, fact gate, rights guard, seal, one verifier, publisher.

Nothing in this package calls a model. Every gate here is plain Python over bytes and event
lists, so its verdict can be recomputed by anyone holding the same inputs. The storage layer is a
protocol (`store.Store`) with an in-memory implementation; the Cosmos adapter comes later and must
keep the same create-only and etag compare-and-set semantics.
"""
