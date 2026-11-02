# Amazon Bedrock setup and bounded smoke test

Status: **no live Bedrock call has been made by this project.** Scenario G is pending explicit
authorization from the account owner.

## Configure

1. Authenticate with any AWS credential-provider-chain method (`aws login`, SSO, env vars). Prefer a
   dedicated profile so you do not overwrite your default: `aws login --profile edge-bedrock`, then
   `$env:AWS_PROFILE = "edge-bedrock"`.
2. Choose a region where your account has Bedrock model access, e.g. `$env:AWS_REGION = "ap-south-1"`.
3. Find a model or inference profile you may invoke (read-only listing calls, no inference charge):
   `aws bedrock list-foundation-models --region <region> --by-inference-type ON_DEMAND` and
   `aws bedrock list-inference-profiles --region <region>`. Some models can only be called through an
   inference profile id. Set `$env:BEDROCK_MODEL_ID` to that exact id. Model availability and access
   grants vary per account/region; verify before relying on one.
4. Optionally set `INPUT_PRICE_PER_MTOK` / `OUTPUT_PRICE_PER_MTOK` from the provider's price list to
   get cost estimates in `/usage`. They are estimates, not a bill.

## Minimum IAM permissions

Strands streams with the Converse API, which requires `bedrock:InvokeModelWithResponseStream`
(and `bedrock:InvokeModel` for non-streaming calls) on the specific model or inference-profile ARN.
Nothing else is needed. Do not use an admin role for routine use.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
    "Resource": ["<model or inference-profile ARN>"]
  }]
}
```

## Suggested bounded smoke test (needs your explicit go-ahead)

Cost bound: each model call is capped at 4096 output tokens, and a turn at 20 model calls and 32768 output tokens in total. The steps
below use tiny prompts, an empty workspace and no tools that take actions.

1. `harness doctor` (no AWS call).
2. `mkdir $env:TEMP\bedrock-smoke; harness ask "Reply with the single word ok." --workspace $env:TEMP\bedrock-smoke`
   Expect streamed text and a `Input tokens / output tokens` line.
3. `harness chat --workspace $env:TEMP\bedrock-smoke`, send `Say hello`, then `/usage` and `/exit`.
4. Optional: in a small throwaway repo, ask it to explain one file and confirm citations.
5. Record the model id, region, token counts and result in `harness-docs/harness/build-log.md`.

Stop after step 3 if anything looks wrong. Only after a pass should `/compact` (one extra call) be
tried.
