/** Local bytes only. This is a proposed dependency manifest, never an applier. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
if (process.argv.slice(2).join(' ') !== '--local-only') throw Error('explicit --local-only required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const digest = data => createHash('sha256').update(data).digest('hex');
const entry = (path, action) => ({ path, sha256: digest(readFileSync(resolve(root,path))), action });
const policyMarker=readFileSync(resolve(root,'core/automation/higgsfield_banner.py'),'utf8')
  .match(/^POLICY = ("[^"\r\n]+")$/m);
if (!policyMarker) throw Error('actual renderer policy marker missing');
const plan = {
  schema: 'daily-private-rollout-plan@1', production_ready: false, scope: 'ybo_v1',
  quality_policy: JSON.parse(policyMarker[1]),
  clients: ['yellow','babylon','origintrail'], maximum_initial_cards_per_kst_day: 3,
  start_kst: '09:00', proposed_cron_utc: '*/15 * * * *', activate_cron: false,
  initial_card_parts: 4, official_publication: false,
  historical_dependencies: [
    '20260906100000_content_ops_review_outbox.sql',
    '20260916190000_content_ops_review_producer_binding.sql',
    '20261003143000_content_ops_reconcile_producer_binding.sql',
    '20261003143100_content_ops_review_exact_copy.sql',
  ].map(name => entry('supabase/migrations/'+name,'compare_hosted_history_and_current_function_only')),
  preexisting_initial_card_contract_sources: [
    'content_ops_button_review_state',
    'content_ops_button_edit_reply', 'content_ops_button_prompt_registration',
    'content_ops_button_durable_attempt', 'content_ops_button_card_send_ledger',
    'content_ops_button_card_owner_gateway',
  ].map(name => entry('supabase/proposals/'+name+'.sql','do_not_reapply_existing_initial_card_contracts_match')),
  ordered_missing_proposals: ['quality_banner_generation_context','content_ops_daily_private_scope']
    .map(name => entry('supabase/proposals/'+name+'.sql','apply_only_exact_new_delta_after_authorization')),
  catalog_inventory: entry('supabase/proposals/content_ops_daily_private_readonly_inventory.sql','read_only_pre_and_post'),
  expected_catalog: entry('ops/content-ops/daily-private-expected-catalog.json','compare_definitions_and_acl'),
  observed_hosted_inventory: entry('ops/content-ops/daily-private-hosted-inventory-20261012.json','observed_catalog_only_not_deployed_readiness'),
  accepted_budget: { mode:'authenticated_provider_estimate@1', maximum_daily_estimate_usd:10,
    final_charge_variance_accepted:true, policy_sha256:'000a4243bf92c082db1120b6ccbd6aa8e2ade95ed355aaef124f79a31c6aa0a4' },
  deployment_config: entry('ops/content-ops/railway.daily-private-card.json','off_deploy_only'),
  remaining_gates: ['exact_release_and_hosted_ci','two_proposal_delta_and_postapply_catalog_comparison',
    'quality_api_durable_volume_and_trusted_references','existing_bot_callback_compatible_private_runtime',
    'off_validate_only_and_authenticated_sha','single_private_card_arrival_receipt'],
};
console.log(JSON.stringify({ ...plan, plan_sha256: digest(JSON.stringify(plan)) },null,2));
