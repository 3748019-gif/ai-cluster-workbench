"""Conservative mapping of the supplied 64 design cases to actual run logs.

This does not execute tests or upgrade NOT_RUN by proximity. A mapped assertion
must appear as a successful test in a supplied raw log; partial scope stays
PARTIAL even if that assertion passed. New mappings can be reviewed explicitly.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

HERE=Path(__file__).resolve().parent
DEFAULT_MATRIX=(HERE/'fixtures/engineering_20261002/02_验收场景矩阵.jsonl' if (HERE/'fixtures/engineering_20261002').is_dir()
                else HERE.parents[1]/'input/02_验收场景矩阵.jsonl')

# Values are specification-to-assertion links, not a count of unique defects.
MAP={
 'REF-01':(['test_docx_release_ids_registered_unknown_and_special_forms'],'PASS','Registered complete REL positive'),
 'REF-02':(['test_docx_release_ids_registered_unknown_and_special_forms'],'PASS','Unknown complete REL negative'),
 'REF-03':(['test_unknown_old_school_reference_is_active_negative'],'PASS','Unknown old school-qualified token'),
 'REF-04':(['test_docx_bare_short_ids_resolve_exactly_without_inferred_school'],'PASS','Bare ID multiple namespaces rejected'),
 'REF-05':(['test_docx_bare_short_ids_resolve_exactly_without_inferred_school'],'PASS','Bare ID unique registered namespace passes'),
 'REF-06':(['test_split_runs_fullwidth_adjacent_registered_references'],'PASS','Complete visible citation spans multiple Word runs'),
 'REF-07':(['test_split_runs_fullwidth_adjacent_registered_references'],'PASS','Fullwidth brackets and adjacent registered IDs'),
 'REF-08':(['test_unknown_header_footer_footnote_endnote_table_and_hyperlink_text'],'PASS','Active references in these explicit OOXML parts all rejected when unknown; no rendered visual QA'),
 'REF-09':(['test_docx_release_ids_registered_unknown_and_special_forms'],'PASS','Explicitly registered special prefixes pass; acquisition not verified'),
 'REF-10':(['test_explicit_hash_bound_history_excluded_but_current_cannot'],'PASS','Explicit hashed historical role may be excluded only from explicit current document set; current cannot hide'),
 'PUB-01':(['test_byte_exact_A_binding_delivers_and_retry_is_idempotent'],'PARTIAL','Current generated release status exact DELIVERED/unknown policy; historical pending facts require separate business status projection check'),
 'PUB-02':(['test_unknown_disclosure_is_not_underlying_verified'],'PARTIAL','Offline exact claim acceptance preserves unknown data; not general policy truth proving'),
 'PUB-03':(['test_unbound_unrelated_tree_cannot_final_deliver'],'PASS','Current final delivery path refuses unbound unrelated release tree'),
 'PUB-04':(['test_exact_registered_source_id_map_is_replayable','test_docx_reference_replay_preserves_all_other_parts_and_unknown'],'PASS','Replayable exact registered ID mechanical transform passes; DOCX parts remain unchanged'),
 'PUB-05':(['test_arbitrary_body_rewrite_never_mechanical','test_map_cannot_delete_unknown_or_change_facts','test_semantic_word_map_rejected_even_registered'],'PASS','Mechanical label cannot authorize body facts rewrite/unknown removal'),
 'PUB-06':(['test_frozen_payload_tamper_blocks_audit','test_T26_changed_actual_tree_rejects_both_initial_and_existing_registration'],'PASS','Frozen registered bytes and tree changes rejected'),
 'PUB-07':(['test_nested_json_bytes_in_txt_manifest_migration_is_hash_bound_and_read_only'],'PARTIAL','One synthetic nested migration; exact historical 17-entry replay is separate and not asserted here'),
 'PUB-08':(['test_nested_json_bytes_in_txt_manifest_migration_is_hash_bound_and_read_only','test_casefold_basename_collision_is_not_guessed_and_exact_paths_pass'],'PARTIAL','Nested migration and ambiguous paths separately covered; not an exhaustive ambiguous migration compound'),
 'PUB-09':(['test_unbound_extra_file_and_duplicate_output_rejected','test_zip_extra_duplicate_or_wrong_prefix_rejected'],'PASS','Extra unbound tree file/ZIP member and duplicate output rejected'),
 'PUB-10':(['test_manifest_self_exclusion_and_self_reference_rejection'],'PASS','Manifest excludes itself and rejects explicit self entry'),
 'CON-01':(['test_preflight_collects_missing_and_wrong_hash_before_model_intent'],'PASS','Required missing/hash corruption collected before model intent; exact original 45 historical files not re-researched'),
 'CON-02':(['test_identity_and_candidate_hash_tamper_retains_raw_without_completing','test_changed_handoff_rejected'],'PASS','Exact candidate/input hash conflicts rejected'),
 'CON-03':(['test_bad_original_and_missing_actual_completion_rejected'],'PASS','HTML masquerading PDF rejected'),
 'CON-04':(['test_search_snippet_does_not_verify_claim'],'PARTIAL','Search snippet cannot verify; arbitrary omitted qualification requires human semantic check'),
 'CON-05':(['test_conflicting_identity_and_contract_version_rejected'],'PASS','Frozen identity/schema version conflict rejected'),
 'CON-06':(['test_generated_member_review_controls_explain_optional_scope_values_and_exact_closure'],'PASS','Reviewer complete field definitions and semantic closure contract'),
 'CON-07':(['test_local_id_alias_is_exact_opt_in_and_retains_raw_and_repair'],'PASS','Unique alias adaptation/raw retained'),
 'CON-08':(['test_local_id_alias_conflicts_duplicates_and_invalid_values_are_not_repaired','test_T09_false_string_is_false_without_access_upgrade'],'PASS','Conflicts rejected and false string narrow adaptation'),
 'CON-09':(['test_access_note_and_snapshot_date_preserved_without_invented_time'],'PASS','Observed date kept without fabricated time'),
 'CON-10':(['test_T08_collect_all_independent_errors_and_skip_prerequisites'],'PARTIAL','Multiple independent errors collected; historical 133 exact count not asserted'),
 'CON-11':(['test_one_missing_source_ten_impacts_one_root_keeps_original'],'PASS','Ten missing-source effects grouped one root; original diagnostic retained'),
 'CON-12':(['test_T09_gap_remains_blocking_not_confirmed_error'],'PARTIAL','Reported evidence gap blocks; controller cannot prove arbitrary natural-language entailment'),
 'EDGE-01':(['test_two_members_one_version_and_other_year_distinct'],'PARTIAL','URL version distinction; same URL/declared version changing bytes needs separate new test'),
 'EDGE-02':(['test_wrong_year_reuse_rejected_without_mutating_request'],'PARTIAL','Wrong identity reuse refused; same filename two-source compound not specifically run'),
 'EDGE-03':(['test_two_consumers_one_original_each_ack_independent_and_no_wakeup'],'PASS','Two subscriptions retain independent precise acknowledgements; no automatic model wake'),
 'EDGE-04':(['test_partial_batch_one_failure_keeps_success_ready'],'PASS','Successful and failed items distinct; related dependencies only affected'),
 'EDGE-05':(['test_bad_original_and_missing_actual_completion_rejected'],'PARTIAL','HTML/invalid JSON/completion absence covered; .partial specific extension not separately asserted'),
 'EDGE-06':(['test_repeat_completion_idempotent_and_modified_bytes_rejected'],'PASS','Exact completion replay idempotent; changed bytes not silently reused'),
 'EDGE-07':(['test_partial_batch_rolling_receipt_no_fake_read','test_denial_or_not_consumed_and_wrong_hash_remain_unknown'],'PASS','Published receipt is not consumption; denied/wrong exact hash unknown'),
 'EDGE-08':(['test_late_previous_task_raw_cannot_be_latest_current_candidate'],'PASS','Late old task raw in latest-named file retained but rejected against exact current ticket'),
 'STATE-01':(['test_uncertain_recover_never_resends_or_steals_stale_lock'],'PASS','Post-intent fresh recovery retains unknown/no resend'),
 'STATE-02':(['test_uncertain_reconciliation_requires_observation'],'PASS','Explicit fresh not-sent enables new ticket'),
 'STATE-03':(['test_T07_capture_survives_parse_failure_and_no_resend'],'PASS','Generation/capture succeed independently of ingestion failure'),
 'STATE-04':(['test_uncertain_recover_never_resends_or_steals_stale_lock'],'PASS','Stale lock retained until explicit reconciliation; cannot steal'),
 'STATE-05':(['test_manifest_projection_repaired'],'PASS','Projection regenerated from authoritative SQLite'),
 'STATE-06':(['test_optional_cleanup_denied_preserves_work'],'PASS','Optional permission denial does not stop task'),
 'STATE-07':(['test_self_review_not_independent_after_model_label_change'],'PASS','Model name switch does not change contributor identity'),
 'STATE-08':(['test_adjacent_task_numbers_do_not_infer_superseded','test_T25_append_relationship_without_rewriting_failed_history'],'PASS','Adjacent IDs infer no supersession; explicit evidenced handled relation appends without rewriting old state'),
 'STATE-09':(['test_T26_actual_tree_before_registration_and_idempotent_retry'],'PASS','Registration explicitly not user downloaded/read'),
 'BUD-01':(['test_atomic_concurrent_reservations_and_stale_cas'],'PASS','CAS atomic shared session prevents two simultaneous spending claims'),
 'BUD-02':(['test_balance_increase_reset_preserves_session_and_limit'],'PASS','Balance increase does not reset this session cumulative use'),
 'BUD-03':(['test_pending_or_unknown_cost_blocks_new_dispatch'],'PASS','Pending/unknown settlement holds new reservations'),
 'BUD-04':(['test_stable_roles_swap_only_future_tasks','test_T03_dynamic_permissions_and_source_quotes'],'PARTIAL','Role snapshots and open default verified; temporary maintenance exit must additionally be observed'),
 'BUD-05':(['test_500_reassessment_800_stop_and_actual_overestimate_preserved','test_uncertain_estimate_is_not_guaranteed_and_near_limit_rejects'],'PASS','500 reassessment and 800 reserve stop; actual over-estimate preserved rather than fake hard guarantee'),
 'BUD-06':(['test_T03_dynamic_permissions_and_source_quotes','test_reviewer_original_sources_and_no_private_answer'],'PARTIAL','Untrusted quoted policy cannot alter compiled controls; real model prompt-injection resistance untested'),
 'BUD-07':(['test_escape_absolute_and_external_symlink_are_not_resolved_outside_root','test_reviewer_original_sources_and_no_private_answer'],'PARTIAL','Release path escape/symlink and private-answer omission; not OS-wide secret prevention'),
 'SEM-01':(['test_selector_change_enters_old_unreferenced_group','test_T16_exact_ratio_neighbor'],'PARTIAL','Independent fixture selector + actual semantic ratio guard; no generic production group dependency engine claimed'),
 'SEM-02':(['test_T13_and_T30_substantive_wording_cannot_escape'],'PASS','Grouped-choice semantic change remains blocking, punctuation passes'),
 'SEM-03':(['test_T14_wrong_college_route_is_gap_correct_scope_passes'],'PASS','Explicit cross-college/degree mismatch blocks'),
 'SEM-04':(['test_unknown_disclosure_is_not_underlying_verified'],'PARTIAL','Fixture publication/adoption unknown preserved; arbitrary adoption inference is human review'),
 'SEM-05':(['test_T15_edit_date_and_outreach_must_not_gain_academic_scope'],'PASS','Page date not inferred academic applicability'),
 'SEM-06':(['test_live_does_not_accept_simulated_task'],'PASS','Test authority cannot be used by live controller'),
 'SEM-07':(['test_unknown_metrics_are_null'],'PASS','Unavailable cost/token/edit measures null'),
}


def parse_logs(paths):
    found={}
    for value in paths:
        p=Path(value);text=p.read_text(errors='replace')
        pending=None
        for number,line in enumerate(text.splitlines(),1):
            match=re.match(r'^(test_[^\s]+)\s+\(([^\n]+)\)\s+\.\.\.\s+(.*)$',line)
            if match:
                pending=(match[1],match[2],number)
                tail=match[3]
                if tail in ('ok','FAIL','ERROR') or tail.startswith('skipped'):
                    status=tail
                else:continue  # e.g. Duplicate ZIP warning interrupts the line.
            elif pending and (line in ('ok','FAIL','ERROR') or line.startswith('skipped')):
                status=line
            else:continue
            method,name,start=pending
            found.setdefault(method,[]).append({'test_name':name,'status':status,'raw_log':str(p),
                'start_line':start,'completion_line':number,'log_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
            pending=None
    return found


def main():
    p=argparse.ArgumentParser();p.add_argument('--matrix',default=str(DEFAULT_MATRIX));p.add_argument('--before-log',action='append',default=[])
    p.add_argument('--after-log',action='append',default=[]);p.add_argument('--extra-map');p.add_argument('--output',required=True)
    p.add_argument('--rollback-result')
    a=p.parse_args();mapping=dict(MAP)
    if a.extra_map:mapping.update({k:(v['tests'],v['max_status'],v['limits']) for k,v in json.loads(Path(a.extra_map).read_text()).items()})
    before=parse_logs(a.before_log);after=parse_logs(a.after_log);cases=[]
    for line in Path(a.matrix).read_text().splitlines():
        original=json.loads(line);cid=original['case_id'];names,max_status,limits=mapping.get(cid,([], 'NOT_RUN','No specific complete assertion mapped/run'))
        b={name:before.get(name,[]) for name in names};f={name:after.get(name,[]) for name in names}
        all_after=bool(names) and all(any(r['status']=='ok' for r in f[name]) for name in names)
        status=max_status if all_after else 'NOT_RUN'
        old_all=bool(names) and all(any(r['status']=='ok' for r in b[name]) for name in names)
        failed_before=any(any(r['status'] in ('FAIL','ERROR') for r in b[name]) for name in names)
        transition=('before_negative_reproduced_after_passed' if failed_before and all_after else 'already_present_and_rerun' if old_all and all_after else 'new_api_or_no_comparable_before' if all_after else 'not_completed')
        cases.append({**original,'status':status,'mapped_assertions':names,'before_evidence':b,'after_evidence':f,
                      'transition':transition,'scope_limits':limits,'real_gui_tested':False,
                      'note':'Matrix is design; mapped reused assertions do not equal 64 independent defects or real calls.'})
    if a.rollback_result:
        rp=Path(a.rollback_result);rr=json.loads(rp.read_text())
        if all(rr.get(k) is True for k in ('passed','code_and_templates_restored_exactly','new_task_raw_artifacts_preserved')) and rr.get('old_controller_recovery_exit')==0 and rr.get('old_controller_recovery',{}).get('audit',{}).get('passed') is True:
            row=next(x for x in cases if x['case_id']=='STATE-10')
            row.update(status='PASS',transition='isolated_rollback_rehearsal_passed',
                scope_limits=rr['compatibility_limits'],mapped_assertions=['rollback_rehearsal.py actual recover/audit'],
                after_evidence={'rehearsal':{'result_path':str(rp),'sha256':hashlib.sha256(rp.read_bytes()).hexdigest(),'command':rr['command']}})
    summary={key:sum(x['status']==key for x in cases) for key in ('PASS','PARTIAL','NOT_RUN')}
    body={'case_count':len(cases),'summary':summary,'cases':cases,'independent_real_external_calls':None,
          'quality_or_speed_improvement_percent':None,'coverage_source':'Raw successful unittest observations only'}
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(body,ensure_ascii=False,sort_keys=True,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
