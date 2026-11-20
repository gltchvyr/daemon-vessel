# H2 correction coverage ledger

Every row names its direct evidence. H1 tests are listed only for H1 behavior;
they are not used as substitutes for an H2-specific assertion.

| Contract requirement | Exact H2 evidence |
|---|---|
| Valid normalized pilot, packet, return, ACK, and complete verify | `H2Tests.test_full_chain_is_dated_immutable_and_verifiable` |
| Admission required fields and oversized pilot refusal | `H2Tests.test_admission_schema_and_oversized_pilot_refuse_without_artifacts` |
| Source digest mismatch before home creation | `H2Tests.test_refuses_digest_schema_collision_cross_session_and_expired_ack` |
| Surface-derived native event ID, local room derivation, thread retention | `H2Tests.test_provenance_preserves_surface_native_thread_and_rendered_fidelity` |
| Rendered fidelity without raw-byte upgrade | `H2Tests.test_provenance_preserves_surface_native_thread_and_rendered_fidelity` |
| Context-packet schema and deterministic scoped digest | `H2Tests.test_full_chain_is_dated_immutable_and_verifiable`; `H2Tests.test_verifier_refuses_tampered_truncated_missing_and_orphaned_artifacts` |
| Construction receipt links packet and source | `H2Tests.test_full_chain_is_dated_immutable_and_verifiable` |
| Return event and submitted-to-gateway delivery receipt | `H2Tests.test_full_chain_is_dated_immutable_and_verifiable` |
| Wrong, missing, cross-session, expired, replayed ACK behavior | `H2Tests.test_duplicate_and_wrong_ack_are_attributable_or_refused`; `H2Tests.test_refuses_digest_schema_collision_cross_session_and_expired_ack` |
| Divergent replay under same native ID | `H2Tests.test_refuses_digest_schema_collision_cross_session_and_expired_ack` |
| Source, packet, return, delivery interruption recovery | `H2Tests.test_retry_and_every_publication_boundary_preserve_chain_ids` |
| ACK/observation interruption recovery | `H2Tests.test_ack_observation_boundary_recovers_without_second_challenge` |
| Concurrent/repeated pilot replay retains one random persisted challenge | `H2Tests.test_concurrent_and_repeated_pilot_replay_keep_one_random_challenge` |
| Missing phase, semantic receipt tamper, and dead-letter tamper | `H2Tests.test_verifier_refuses_missing_phase_semantic_tamper_and_dead_letter_tamper` |
| Separate H2 lock; no nested H1 lock | `H2Tests.test_h2_lock_is_separate_from_h1_ingest_lock` |
| Missing delivery receipt | `H2Tests.test_verifier_refuses_missing_delivery_receipt` |
| Tampered packet, delivery, observation, phase marker | `H2Tests.test_verifier_refuses_tampered_delivery_observation_and_phase_marker`; `H2Tests.test_verifier_refuses_tampered_truncated_missing_and_orphaned_artifacts` |
| Unreadable, truncated, missing, orphaned packet artifacts | `H2Tests.test_verifier_refuses_tampered_truncated_missing_and_orphaned_artifacts` |
| H1 source/return/ACK event and H1 receipt chain reconstruction | `H2Tests.test_full_chain_is_dated_immutable_and_verifiable` |
| H1 stale/live and Windows process-lock behavior | `CrossingTests.test_stale_lock_recovers_and_live_lock_refuses` |
| H1 duplicate attribution, transaction recovery, immutable receipts, tamper detection | `CrossingTests.test_duplicate_and_replay_receipt_relationships_are_attributable`; `test_crash_between_publications_is_recovered_as_rollback`; `test_crash_after_both_publications_is_recovered_as_commit`; `test_receipt_tampering_and_missing_artifact_refuse` |
| External schema `$ref`, unsafe repository/worktree/symlink paths | `CrossingTests.test_external_schema_reference_refuses_without_retrieval`; `test_repository_worktree_and_symlink_escape_refuse` |
| CLI sanitized refusal/nonleak | `CrossingTests.test_cli_help_append_and_private_nonleak`; `test_cli_malformed_inputs_and_digest_refuse_without_traceback` |
| Unknown-ACK attempt records validate when intact and reject movement/tampering | `H2Tests.test_unknown_ack_attempt_is_verifiable_then_moved_or_tampered_refuses` |
| Source/tool actor, speaker, runtime, and native-ID attribution bind confirmation | `H2Tests.test_confirm_and_verify_bind_h1_actor_runtime_identity` |
| Replay/duplicate receipt identity resolution, original observation linkage, and cross-transaction refusal | `H2Tests.test_replay_receipt_links_are_resolved_and_transaction_bound` |

## Plugin matrix

| Requirement | Exact evidence |
|---|---|
| Unmatched, non-Discord, actor mismatch, and lookalikes return `None` | `PluginTests.test_unmatched_non_discord_mismatch_and_lookalikes_are_untouched` |
| Exact pilot and ACK normalization/handling | `PluginTests.test_exact_pilot_and_ack_are_normalized_and_handled` |
| argv, no shell, private staging before daemon | `PluginTests.test_exact_pilot_and_ack_are_normalized_and_handled` |
| Exact daemon-reply grammar and private sentinel refusal | `PluginTests.test_grammar_and_private_sentinel_are_refused` |
| Missing admission evidence and post-daemon failure sanitization | `PluginTests.test_missing_runtime_and_matched_failure_are_sanitized` |
| Cleanup occurs before confirmation/submission | `PluginTests.test_cleanup_precedes_confirm` |
| Bounded timeout/stdout/stderr and cleanup failure | `PluginTests.test_bounded_subprocess_and_cleanup_failure` |
| Manifest parsing and temporary-Hermes-home discovery | `PluginTests.test_doctor_requires_temporary_home_and_hook_surface`; `PluginTests.test_manifest_and_hook_surface` |
| Unavailable post-admission hook surface refusal | `PluginTests.test_doctor_requires_temporary_home_and_hook_surface` |

Live Discord delivery and installation/enabling Hermes remain unavailable by
design. Offline discovery and refusal are required and tested.
