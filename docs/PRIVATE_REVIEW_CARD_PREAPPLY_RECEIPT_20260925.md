# Private button-card post-correction readback

Observed on 2026-09-25 UTC against the linked `coineasy-meme-engine`
production project. This document records read-only checks after the separately
approved producer-binding correction; it is not an approval to apply the
button-card SQL proposals, deploy a service, enable a flag or send a message.

The exact one-migration runner applied version `20260916190000` from GitHub
`main` `ab5dca31b0f770a332c2a6ec3ea258930dec70ff` with migration SHA-256
`dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6`.
Its operation ID was `0371b727-df20-47a4-a628-5878daefebdb`; its final
receipt SHA-256 was
`9c3b8b23f784d741321a0c0a69c9b1e79e6c477a29d05a1546798884c62e7a02`.
The runner reported `corrected_exact_history`, `runtimeActivationAllowed=false`
and `providerCalls=0`. An independent subsequent read-only contract query
again returned `corrected_exact_history`, `read_only=true`, `changes=0` and
`provider_calls=0`. Do not run that one-shot operation again.

The strengthened full pre-apply SQL
`supabase/proposals/content_ops_button_card_preapply_readonly.sql` (SHA-256
`2a08dce306ee50b9f4e992321ca264abdcd26b22f8caaa05a9d817cafd913334`)
now returned `button_card_preapply=pass`, `read_only=true`,
`production_changes=0` and `provider_calls=0`. It pins the corrected candidate
reader and five other base function bodies, the existing outbox/asset/Storage
column shapes, base RPC ACLs, FORCE RLS and absence of a partial button-card
owner installation. This supersedes the earlier failing *current-state*
decision in the [2026-09-23 receipt](PRIVATE_REVIEW_CARD_PREAPPLY_RECEIPT_20260923.md);
that receipt remains valid historical evidence of the previous hosted state.

The separate prompt-capability catalog gate
`supabase/proposals/content_ops_button_prompt_preapply_readonly.sql` (SHA-256
`d25ca23042799e5754da8e0a50c5c0c0943a723d94128e25d3ab0743efda1a5b`)
returned `prompt_preapply=pass`, `read_only=true`, `changes=0`,
`provider_calls=0` and `hosted_runtime_verified=false`.

**Decision:** the producer-binding mismatch is resolved and both existing-base
pre-apply catalog gates pass. This is necessary but not sufficient for a live
private card. The button-card send-ledger and owner-gateway files remain SQL
proposals, not applied migrations; the prompt capability wrappers remain
unapplied. Hosted execution of those proposals, exact ACL/post-apply checks,
the claim-bound Storage reader, default-OFF runtime deployment, exclusive
delivery ownership and one authorized private Telegram canary remain separate
gates. Neither private review check authorizes official-channel publication.
