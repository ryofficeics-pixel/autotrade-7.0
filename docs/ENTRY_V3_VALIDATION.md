# Entry V3 validation

V3 is `SHADOW`, not a candidate or champion. `execution_enabled` is rejected unless false.

Decision-time features come from normalized causal events. Candidate records retain the dataset ID, source event hash, feature schema version, strategy version, execution-model version, config hash, and source fingerprint. Labels, calibrated models, purged validation, and promotion require a separate deterministic replay dataset and remain incomplete.
