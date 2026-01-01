# ARED Edge — IOTA Anchoring Service

Short description
The IOTA Anchoring Service periodically anchors Merkle roots or aggregated event hashes from the ARED Edge platform to the IOTA Tangle. It is a lightweight integration service designed to provide an immutable public anchor for off-chain verification and auditability.

What this system does
- Consume normalized events or block/transaction hashes from the Substrate indexer or event stream.
- Build deterministic data digests (Merkle tree roots or aggregated hashes) that summarize a batch or time window of events.
- Publish the digest to the IOTA Tangle (post a message/transaction containing the root) and capture the transaction/message ID.
- Persist anchor metadata (digest, timestamp, Tangle message ID, status, proof links) to the off-chain DB.
- Provide APIs and tooling to verify anchors, reconcile failures, and re-anchor if needed.

Primary responsibilities
- Data aggregation: collect event hashes for a configured window (e.g., daily) and compute a Merkle root or combined digest.
- Tangle posting: sign (if applicable) and post the computed digest to the IOTA network.
- Persistence: store anchor records, references to the original events, and IOTA message IDs in a durable DB.
- Reconciliation & retry: detect failed or unconfirmed posts and retry with exponential backoff or manual reconciliation.
- Verification API: allow clients to verify that a given event or hash is included in a posted anchor (via Merkle proofs or recorded metadata).

Key components
- Consumer: subscribes to the event stream (Kafka / DB notifications / HTTP) and collects hashes.
- Aggregator: computes Merkle trees or batch digests according to configured schedules.
- Poster: interfaces with IOTA client libraries to publish anchors to the Tangle and handle responses.
- Store: off-chain DB (Postgres) to record anchors, proofs, statuses, and audit logs.
- API / CLI: endpoints to trigger on-demand anchors, query anchor history, and verify inclusion proofs.
- Scheduler: CRON or job runner that triggers periodic anchoring (e.g., daily at UTC midnight).

APIs & interfaces
- /anchors POST: trigger an immediate anchor job (returns job id and status).
- /anchors GET: list recent anchors with status, digest, and message IDs.
- /anchors/{id} GET: get anchor details, linked events, and Merkle proofs.
- /verify POST: accept an event hash + proof and return verification result.
- Internal subscription: listens to indexer event stream or database change feed to gather hashes.

Data model (example)
- anchor: { id, digest, method, start_time, end_time, iota_message_id, status, created_at }
- anchor_item: { anchor_id, event_id, event_hash, position_in_merkle }
- proof: { anchor_id, event_hash, merkle_proof, verification_status }

Operational behavior & guarantees
- Idempotency: repeated runs for the same time window should not create duplicate anchors (use deterministic digests and idempotency keys).
- Atomicity: anchors should be inserted into DB with a clear status (pending, posted, confirmed, failed).
- Retries: network or IOTA node failures trigger retries with backoff; persistent failures are marked for manual reconciliation.
- Security: signing keys (if used) and IOTA credentials are stored in K8s secrets.

Deployment & runtime
- Designed as a small container or Kubernetes CronJob depending on usage:
  - CronJob mode: run as scheduled job (daily anchor, quick run).
  - Long-running mode: continuous consumer that aggregates and posts when threshold/time reached.
- Low resource usage; stateless aside from DB and ephemeral cache.
- Secrets (IOTA seed or auth) provided via K8s secrets; network egress to IOTA nodes required.

Local development (quickstart)
- Configure INDEXER_DB_URL or EVENT_SINK connection for consuming event hashes.
- Set IOTA_NODE_URL and IOTA_SEED via environment or .env for local testing.
- Run aggregator in local dev mode: python -m app or go run ./cmd/anchor (depending on implementation).
- Use provided test vectors to verify proof generation and verification logic.

Observability & monitoring
- Basic metrics: anchors posted, post failures, confirmation latency.
- Logs: structured logs for each anchor job with digest and response from IOTA.
- Health endpoints and alerts for repeated failures or missed schedules.

Testing & CI
- Unit tests for digest and Merkle proof generation.
- Integration tests that simulate event ingestion and verify posted anchor metadata.
- End-to-end tests optionally exercise posting to an IOTA testnet node or mock.

Who owns it
- Primary: Integrations / Platform Team (OWNERS file).
- Secondary: Security & Compliance for auditability.

Examples & verification
- Given event hashes H1..Hn:
  - Aggregator computes Merkle root R.
  - Poster publishes R to IOTA and returns message_id M.
  - Store records {R, M, included_hashes=[H1..Hn], proofs}.
  - Verification: given Hi, use proof to reconstruct R and confirm that R is posted at M on the Tangle.

Files & layout (high level)
- cmd/ or service/: service entrypoints and binaries
- consumer/: event subscription adapters
- aggregator/: merkle/digest code
- poster/: IOTA client integration
- db/: schema and migration scripts
- k8s/: CronJob or Deployment manifests
- dev/: local test tooling and mocks

Integration with Edge Services

This service integrates with the edge-iot-mqtt-services repository for event aggregation and anchor storage.

Related Documentation
- [Failure Recovery Matrix](../edge-iot-mqtt-services/docs/FAILURE_RECOVERY_MATRIX.md) - Recovery procedures for IOTA anchor failures
- [Prospect Integration](../edge-iot-mqtt-services/docs/PROSPECT_INTEGRATION.md) - Cloud sync includes anchor references
- [Storage Retention](../edge-iot-mqtt-services/docs/STORAGE_RETENTION.md) - Anchor data retention policies

Prospect Cloud Integration
Anchors are synchronized to Prospect Cloud for external verification:
- Anchor records synced with highest priority (priority 1)
- Includes: digest, method, time range, IOTA message ID, confirmation status
- Proof references attached to telemetry payloads for end-to-end auditability

Failure Recovery
**Failure Recovery:**

- IOTA node unreachable: Detected by post timeout and connection error, recovery via retry with exponential backoff and failover to alternate node
- Anchor post failed: Detected by HTTP error and invalid response, recovery via mark as failed and retry on next schedule
- DB write failed: Detected by transaction error, recovery via rollback and retry anchor creation
- Duplicate anchor: Detected by idempotency check, recovery via skip and return existing anchor ID

License
- Add LICENSE at repository root (choose an appropriate license for your code).
