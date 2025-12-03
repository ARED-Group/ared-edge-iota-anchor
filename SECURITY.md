# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |


## IOTA Security Considerations

### Seed Management
- IOTA seeds stored exclusively in K8s secrets
- Never log or expose seeds
- Rotate seeds periodically

### Network Security
- Use official IOTA nodes or trusted node providers
- Validate all Tangle responses
- Implement retry logic with backoff

### Data Integrity
- Verify Merkle proofs before anchoring
- Validate all event hashes
- Log all anchor operations for audit
