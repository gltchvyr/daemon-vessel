# H3 coverage ledger

Every row maps one implementation requirement to its implementation seam and a
named assertion. All fixtures are disposable external homes; no fixture is a
canonical continuity object or a live surface.

| Requirement | Implementation | Exact test / evidence | Status |
| --- | --- | --- | --- |
| Request boundary: one read, supplied digest, UTF-8/JSON, and Draft 2020-12 refusal | `_read_snapshot`, `build`, `REQUEST` | named subtests in `test_public_build_request_boundary_reads_once_and_refuses_invalid_inputs` | pass |
| Exact pinned historical fixtures | Git blob checkout plus `.gitattributes` | `test_historical_fixtures_are_exact_pinned_blobs` asserts raw SHA-256, blob ID, and terminal byte | pass |
| Public Registry installed snapshot boundary | `registry.validate_installed_snapshot` | `test_public_installed_snapshot_boundaries_are_immutable`, `test_public_installed_snapshot_boundaries_refuse_receipt_tampering` | pass |
| Public Access Index installed snapshot boundary | `access_index.validate_installed_snapshot` | `test_public_installed_snapshot_boundaries_are_immutable`, `test_public_installed_snapshot_boundaries_refuse_receipt_tampering` | pass |
| Detached install receipts and digests | `_installed` | `test_installed_input_tampering_refuses_before_hydration` | pass |
| Stable installed-input snapshot | `_installed` snapshot values | `test_installed_snapshot_survives_replace_between_acquisition_and_use` | pass |
| Explicit known seed | `_hydrate` | `test_unknown_seed_active_entry_typed_edge_and_budget_boundaries` | pass |
| Unknown explicit seed refusal | `_hydrate` | `test_unknown_seed_active_entry_typed_edge_and_budget_boundaries` | pass |
| Active entry enforcement | `_hydrate` | `test_unknown_seed_active_entry_typed_edge_and_budget_boundaries` | pass |
| Entry namespace gate before reads | `_hydrate` | `test_namespace_privacy_refuse_before_local_source_read` | pass |
| Resource privacy gate before reads | `_hydrate` | `test_namespace_privacy_refuse_before_local_source_read` | pass |
| Deterministic authority order and mirror fallback | `_resource_locators`, `_hydrate` | `test_locator_mirror_pointer_and_source_ceiling` | pass |
| Ordered fallback only for thin branch | `_hydrate` | `test_deterministic_seed_authority_fallback_and_cap_semantics` | pass |
| Intent-relevant typed links from grounded nodes | `_INTENT_EDGES`, `_hydrate` | `test_grounded_nodes_still_follow_relevant_typed_edges` | pass |
| Four-hop hard ceiling | `_hydrate`, `MAX_HOPS` | `test_deterministic_seed_authority_fallback_and_cap_semantics` | pass |
| Twelve-resource hard ceiling | `_hydrate`, `MAX_RESOURCES` | `test_unknown_seed_active_entry_typed_edge_and_budget_boundaries` | pass |
| Caller-lower budget enforcement | `_hydrate` | `test_deterministic_seed_authority_fallback_and_cap_semantics` | pass |
| Character budget and span accounting | `_source_read` | `test_bounded_extraction_token_json_and_invalid_json_outcomes` | pass |
| Token budget enforcement | `_source_read`, `_tokens` | `test_bounded_extraction_token_json_and_invalid_json_outcomes` | pass |
| Safe local locator syntax | `_safe_relative` | `test_locator_refusals_are_sanitized` | pass |
| Stale/unreachable/manual/local admission | `_resource_locator`, `_source_read` | `test_unavailable_nonlocal_and_symlink_sources_remain_content_free` | pass |
| Bounded source acquisition | `_source_read`, `MAX_SOURCE_BYTES` | `test_locator_mirror_pointer_and_source_ceiling` | pass |
| Verified hash / absent hash / mismatch | `_source_read` | `test_source_fidelity_hash_utf8_empty_and_unavailable_outcomes` | pass |
| Invalid UTF-8 / empty / unsupported | `_source_read` | `test_source_fidelity_hash_utf8_empty_and_unavailable_outcomes` | pass |
| Valid and invalid JSON | `_source_read` | `test_bounded_extraction_token_json_and_invalid_json_outcomes` | pass |
| Truncation and compaction ledger | `_source_read`, `_make_packet` | `test_bounded_extraction_token_json_and_invalid_json_outcomes` | pass |
| Truthful grounded / partial / unresolved status | `_make_packet` | `test_build_candidate_verify_and_idempotent_retry` | pass |
| Packet and receipt canonical digest | `_canonical`, `_read_artifact` | `test_candidate_collision_and_tamper_refuse` | pass |
| Request/packet/construction recovery | `build`, `_phase` | `test_publication_boundaries_recover_deterministically` | pass |
| Existing packet missing phase repair | `build` existing-packet branch | `test_packet_publication_interruption_recovers_same_chain` | pass |
| Candidate missing receipt/phase repair | `append_h3_candidate` existing-candidate branch | `test_candidate_recovery_boundaries_are_exact_and_byte_preserving` | pass |
| Candidate self-digest at validate and append | `candidate_validate`, `append_h3_candidate` | `test_candidate_collision_and_tamper_refuse` | pass |
| Candidate full packet-chain admission | `append_h3_candidate`, `verify` | `test_candidate_lineage_and_receipt_tampering_refuse` | pass |
| Candidate no-canonical-effect boundary | candidate schema and verifier | `test_candidate_lineage_and_receipt_tampering_refuse` | pass |
| Full-tree artifact path/depth/extension checks | `_artifacts`, `verify` | `test_verifier_refuses_missing_moved_truncated_duplicate_and_phase_artifacts` | pass |
| Phase contents and receipt linkage | `_phase_value`, `verify` | `test_verifier_refuses_missing_moved_truncated_duplicate_and_phase_artifacts` | pass |
| Refusal/dead-letter lineage | `_refusal`, `verify` | `test_divergent_request_id_is_refused_and_dead_lettered` | pass |
| Daemon historical 0.1 validator | `HISTORICAL_DAEMON_PACKET` | `test_actual_historical_packet_boundaries_and_explicit_dispatch` | pass |
| RETURN-POINT historical 0.1 validator | `HISTORICAL_RETURN_POINT_PACKET` | `test_actual_historical_packet_boundaries_and_explicit_dispatch` | pass |
| Wrong-boundary and unknown-version refusal | `validate_packet_version` | `test_actual_historical_packet_boundaries_and_explicit_dispatch` | pass |
| Lock and stale-lock recovery | `crossing.serialization_lock` | `test_h3_live_lock_refuses_and_stale_lock_recovers` | pass |
| External-home smoke and byte-stable retry | `build`, `append_h3_candidate`, `verify` | `test_build_candidate_verify_and_idempotent_retry` | pass |
| Privacy and nonleak | sanitized H3 errors | `test_unsafe_locator_and_digest_refuse_without_content_leak` | pass |
| Canonical request/candidate persistence | `_read_snapshot` | `test_noncanonical_request_bytes_are_refused_before_publication` | pass |
| B3: traversal stops after any resource/character/token exhaustion | `_hydrate` | `test_unknown_seed_active_entry_typed_edge_and_budget_boundaries`, `test_bounded_extraction_token_json_and_invalid_json_outcomes` | pass |
| B4: partial bounded reads use one opened handle | `_source_read` | `test_source_read_handles_injected_partial_reads` | pass |
| B4: final-component link, Windows ancestor junction, handle-resolution failure, and swap-back refusal | `_source_read` | `test_final_component_link_is_refused`, `test_windows_ancestor_junction_substitution_refuses_external_bytes`, `test_windows_handle_resolution_failure_and_swap_back_refuse` | pass |
| B4: source growth beyond byte ceiling | `_source_read` | `test_source_growth_during_read_refuses_bounded_acquisition` | pass |
| B6: refusal identity binds code and attempt digest | `_refusal`, `verify` | `test_refusal_identity_binds_attempt_and_failure_condition`, `test_refusal_attempt_identity_reconstructs` | pass |
| B6: refusal publication recovery is byte-stable | `_refusal`, `_phase` | `test_refusal_publication_boundaries_recover_with_identical_bytes` | pass |
| B7: exact packet reconstruction from durable inputs | `_make_packet`, `_verify_under_lock` | `test_reconstructive_accounting_mutations_refuse_after_redigest` | pass |
| B7: re-digested hops/resources/tokens/source/omission/ledger mutations | `_verify_under_lock` | named subtests in `test_reconstructive_accounting_mutations_refuse_after_redigest` | pass |
| B7: reference, ownership, transaction, and phase-tree adversaries | `_verify_under_lock` | `test_reconstructive_receipt_reference_mutations_refuse`, `test_reconstructive_candidate_ownership_mutations_refuse`, `test_reconstructive_cross_transaction_reference_refuses`, `test_reconstructive_phase_tree_mutations_refuse` | pass |
| B7: duplicate/refusal receipt canonical-ownership rejection | `_verify_under_lock` | `test_duplicate_and_refusal_receipts_cannot_claim_canonical_artifacts` | pass |
| B5: candidate-only / receipt-only / complete recovery semantics | `append_h3_candidate`, `_verify_under_lock` | `test_candidate_recovery_boundaries_are_exact_and_byte_preserving` | pass |
| B5: impossible phase-only, wrong exception, unrelated incomplete, damaged lineage | `_verify_under_lock` | `test_phase_only_candidate_boundary_is_impossible`, `test_recovery_exception_rejects_complete_absent_and_wrong_boundary`, `test_unrelated_incomplete_candidate_blocks_recovery`, `test_recovery_refuses_damaged_durable_lineage_at_each_boundary` | pass |
| B2: complete Registry constituent tamper matrix | `registry.validate_installed_snapshot` | named `registry` subtests in `test_public_installed_snapshot_constituent_tamper_matrix`: data, schema, detached digest, receipt, linkage | pass |
| B2: complete Access Index constituent tamper matrix | `access_index.validate_installed_snapshot` | named `access_index` subtests in `test_public_installed_snapshot_constituent_tamper_matrix`: data, schema, detached digest, receipt, linkage | pass |
| B3: genuine 4→5 hop omission | `_hydrate`, `_make_packet`, `_verify_under_lock` | `test_genuine_four_to_five_hop_omission_is_durable`, `test_four_to_five_hop_omission_redigest_refuses_verification` | pass |
| B4: POSIX component-descriptor ancestor substitution | `_source_read` | `test_posix_ancestor_substitution_refuses_external_bytes` (POSIX-only) | platform-gated |
| B5/B7: reconstruct existing packet before repair | `_verify_existing_packet_for_recovery` | named subtests in `test_existing_packet_recovery_reconstructs_before_repair` | pass |
| B6/B7: intact and malformed refusal-only transactions | `_refusal`, `_verify_under_lock` | `test_refusal_only_trees_reconstruct_for_build_and_candidate_attempts`, `test_refusal_only_tamper_matrix_refuses` | pass |
| B7: schema-valid orphan request rejection | `_verify_under_lock` | `test_verifier_refuses_schema_valid_orphan_request_artifact` | pass |
| Thin fallback prevented at 4→5 hop boundary | `_hydrate`, `_make_packet` | `test_genuine_thin_fallback_four_to_five_omission_is_accounted` | pass |
| Thin fallback prevented by exhausted resource, character, or token budget | `_hydrate` | named resource/char/token subtests in `test_exhausted_budget_records_prevented_thin_fallback` | pass |
| Contract-attributable duplicate candidate receipt and receipt-before-phase recovery | `_derive_duplicate_receipt`, `append_h3_candidate`, `_verify_under_lock` | `test_identical_complete_candidate_retry_publishes_one_attributable_duplicate`, `test_duplicate_candidate_receipt_interruption_recovers_once`, `test_duplicate_receipt_phase_only_publication_boundary_repairs_exactly_once` | pass |
| Duplicate receipt reconstructed identity and lineage | `_derive_duplicate_receipt`, `_verify_under_lock` | named copied-ID, surplus, request/transaction, packet, candidate, original, timestamp/phase subtests in `test_duplicate_receipt_reconstruction_matrix` | pass |
| Duplicate phase-only recovery exception remains narrow | `_verify_under_lock` | named altered-receipt, wrong-phase, altered-phase, surplus-duplicate subtests in `test_duplicate_phase_recovery_exception_is_narrow` | pass |
